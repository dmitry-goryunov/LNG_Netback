"""Decision-state and discrete vessel-programme layer.

This module is deliberately independent of Streamlit.  It preserves the
legacy renewal-rate strip in :mod:`model` and adds explicit cost treatment for
post-lift, pre-lift and programme decisions.

The programme optimiser is a provisional deterministic one-vessel optimiser.
It uses the existing monthly strip economics and route durations.  It does not
yet replace the legacy physical model; the physical/ETS rebuild remains a
separate phase in IMPROVEMENT_PLAN.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence

import pandas as pd

import emissions
import model
import physical


class DecisionMode(str, Enum):
    """Economic state selected before a recommendation is calculated."""

    RENEWAL_RATE_SCREEN = "renewal_rate_screen"
    POST_LIFT_DIVERSION = "post_lift_diversion"
    PRE_LIFT_CARGO = "pre_lift_cargo"
    VESSEL_PROGRAMME = "vessel_programme"


class FirstCargoState(str, Enum):
    """Finer-grained commercial state for the *current* cargo only
    (docs/PHASE2_PLAN.md Section 3a), independent of the physical engine.

    DecisionMode alone can only express "both procurement and loading are
    sunk" (POST_LIFT_DIVERSION/VESSEL_PROGRAMME's current cargo) or "both
    avoidable" (PRE_LIFT_CARGO, any future_cargo=True) -- there was no way
    to represent a cargo that is already bought (procurement committed)
    but not yet loaded (loading still avoidable), a real, distinct
    commercial state (a DES/FOB-purchased cargo sitting pre-loading).
    """

    ALREADY_LOADED = "already_loaded"  # procurement + loading both sunk
    PROCUREMENT_COMMITTED_LOADING_REQUIRED = "procurement_committed_loading_required"  # procurement sunk, loading avoidable
    FULLY_PRE_LIFT = "fully_pre_lift"  # both avoidable -- same treatment as today's PRE_LIFT_CARGO


class CostTreatment(str, Enum):
    INCLUDED = "included"
    SUNK = "sunk"
    NOT_APPLICABLE = "not_applicable"


_COST_TYPES = {
    "procurement",
    "loading",
    "charter",
    "bunkers",
    "canal",
    "port",
    "regas",
    "ets",
    "other",
}


def cost_policy(
    mode: DecisionMode,
    cost_type: str,
    *,
    future_cargo: bool = False,
    first_cargo_state: FirstCargoState | None = None,
) -> CostTreatment:
    """Return the decision-state treatment for one cost category.

    Future cargoes in a vessel programme are new pre-lift decisions and
    therefore include procurement and loading.  The currently loaded cargo in
    POST_LIFT_DIVERSION or VESSEL_PROGRAMME treats them as sunk by default.

    first_cargo_state (Section 3a) is additive: when omitted (the
    default), behaviour is identical to before it existed -- exactly the
    two combinations above. When supplied (and future_cargo is False --
    a future cargo is always fully pre-lift regardless of this argument,
    per existing route_value(..., future_cargo=True) semantics),
    procurement and loading are set independently per state, which is the
    whole point: PROCUREMENT_COMMITTED_LOADING_REQUIRED sinks procurement
    but not loading, a combination mode/future_cargo alone cannot express.
    """

    try:
        mode = DecisionMode(mode)
    except ValueError as exc:  # pragma: no cover - defensive API validation
        raise ValueError(f"unknown decision mode {mode!r}") from exc

    cost_type = cost_type.lower().strip()
    if cost_type not in _COST_TYPES:
        raise ValueError(f"unknown cost type {cost_type!r}")

    if cost_type not in {"procurement", "loading"}:
        return CostTreatment.INCLUDED

    if future_cargo:
        return CostTreatment.INCLUDED

    if first_cargo_state is not None:
        try:
            first_cargo_state = FirstCargoState(first_cargo_state)
        except ValueError as exc:  # pragma: no cover - defensive API validation
            raise ValueError(f"unknown first_cargo_state {first_cargo_state!r}") from exc
        if first_cargo_state == FirstCargoState.ALREADY_LOADED:
            return CostTreatment.SUNK
        if first_cargo_state == FirstCargoState.PROCUREMENT_COMMITTED_LOADING_REQUIRED:
            return CostTreatment.SUNK if cost_type == "procurement" else CostTreatment.INCLUDED
        return CostTreatment.INCLUDED  # FULLY_PRE_LIFT

    if mode in {DecisionMode.POST_LIFT_DIVERSION, DecisionMode.VESSEL_PROGRAMME}:
        return CostTreatment.SUNK
    return CostTreatment.INCLUDED


@dataclass(frozen=True)
class RouteValue:
    route: str
    month_index: int
    load_month: pd.Timestamp
    duration_days: float
    full_cargo_value: float
    incremental_value: float
    procurement_treatment: CostTreatment
    loading_treatment: CostTreatment


@dataclass(frozen=True)
class ProgrammeLeg:
    cargo_number: int
    route: str
    month_index: int
    load_month: pd.Timestamp
    start_day: float
    duration_days: float
    value: float
    decision_mode: DecisionMode

    @property
    def end_day(self) -> float:
        return self.start_day + self.duration_days


@dataclass(frozen=True)
class ProgrammePlan:
    legs: tuple[ProgrammeLeg, ...]
    horizon_days: float
    residual_days: float
    residual_value: float
    total_value: float

    @property
    def sequence(self) -> str:
        return " -> ".join(leg.route for leg in self.legs)

    @property
    def used_days(self) -> float:
        return sum(leg.duration_days for leg in self.legs)


@dataclass(frozen=True)
class ProgrammeResult:
    best: ProgrammePlan
    alternatives: tuple[ProgrammePlan, ...]

    @property
    def next_best(self) -> ProgrammePlan | None:
        return self.alternatives[1] if len(self.alternatives) > 1 else None

    @property
    def advantage(self) -> float | None:
        nxt = self.next_best
        return None if nxt is None else self.best.total_value - nxt.total_value


def _normalise_route(route: str) -> str:
    route_l = route.strip().lower()
    if route_l in {"europe", "nwe", "usgc-nwe", "usgc → nwe"}:
        return "Europe"
    if route_l in {"asia", "jkm", "usgc-jkm", "usgc → jkm"}:
        return "Asia"
    raise ValueError(f"unknown route {route!r}")


def _duration(row: Mapping, route: str) -> float:
    route = _normalise_route(route)
    return float(row["europe_rt"] if route == "Europe" else row["asia_rt"])


def _full_value(row: Mapping, route: str) -> float:
    route = _normalise_route(route)
    return float(row["eu_cargo"] if route == "Europe" else row["asia_cargo"])


def _physical_route_breakdown(row: Mapping, params: model.Params, route: str) -> dict:
    """Itemized $/MMBtu cost lines for one route, physical-engine-derived
    (docs/PHASE2_PLAN.md Section 8 step 8). The single source of truth
    behind both _physical_route_value() (the scalar route_value() uses)
    and physical_waterfall_breakdown() (app.py's waterfall/Sankey charts
    for the three non-screen decision modes) -- computed once so the
    chart always reconciles to the decision value shown next to it,
    unlike calling model.waterfall_breakdown() there (which reads static
    model.strip() columns the non-screen modes no longer use).

    Replaces model.strip()'s static fuel-rate (``residual_laden_vlsfo``/
    ``ballast_fuel``/``port_fuel_rate`` applied flatly regardless of
    congestion) and uniform-0.5 ETS-scope constant with
    physical.run_voyage()'s actual per-segment mass balance and
    emissions.py's per-segment EU-ETS scope. Market prices (``proc``,
    ``ttf_usd``/``JKM``), commercial cost lines (loading, regas/port,
    other_cost, Panama toll) and the snapped/overridden charter day-rate
    all come from ``row``/``params`` unchanged, on the same loaded-cargo
    basis model.strip() uses.

    "Boil-off" is revenue foregone on gas that never reached delivery
    (``cargo - delivered_mmbtu``, valued at the same sale price as
    revenue) -- this keeps model.waterfall_breakdown()'s own "boil-off is
    lost cargo, valued at sale price" narrative, but the mass is now the
    engine's actual per-segment result (net of reliquefaction) instead of
    a flat boil_off_rate*laden_days constant. "Bunkers" is *only* the
    liquid fuel actually purchased (net of free BOG) -- the engine's own
    shortfall output, not a separately pre-netted constant -- so the same
    gas is never charged as both a boil-off loss and a like-for-like fuel
    cost. revenue - sum(v for _, v in lines) == margin exactly, by
    construction (same invariant model.waterfall_breakdown() documents),
    and margin * cargo == _physical_route_value()'s full_cargo_value.

    heel_target_mmbtu=0.0: the legacy model has no heel concept and treats
    100% of ballast/discharge fuel demand as purchased VLSFO with no BOG
    offset (docs/PHASE2_PLAN.md Section 10 item 5/7); zero heel reproduces
    that assumption exactly rather than silently changing it as a side
    effect of this wiring.
    """
    route = _normalise_route(route)
    vessel = physical.vessel_performance_from_params(params)
    fx_l = float(row["fx"])
    load_month = pd.Timestamp(row["load_month"])
    phase = model.phase_for_year(load_month.year)
    charter_rate = float(row["charter"])
    cargo = params.cargo_size

    if route == "Europe":
        segments = physical.europe_route_segments(params)
        price = float(row["ttf_usd"])
    else:
        segments = physical.asia_route_segments(params)
        price = float(row["JKM"])

    ledger = physical.run_voyage(segments, vessel, loaded_mmbtu=cargo, heel_target_mmbtu=0.0)
    voyage_em = emissions.voyage_emissions(ledger)

    boiloff = price * (cargo - ledger.delivered_mmbtu) / cargo
    bunkers = ledger.total_liquid_fuel_tonnes * params.vlsfo_price / cargo
    charter_line = charter_rate * ledger.total_days / cargo

    lines = [
        ("Procurement", float(row["proc"])),
        ("Loading", params.loading),
        ("Charter", charter_line),
        ("Bunkers", bunkers),
        ("Boil-off", boiloff),
    ]
    if route == "Europe":
        ets_line = emissions.ets_cost_usd(voyage_em, params.eua_price, fx_l) * phase / cargo
        lines += [("Discharge", params.eu_regas_port), ("ETS", ets_line), ("Other", params.other_cost)]
    else:
        lines += [
            ("Canal", params.panama_toll_roundtrip / cargo),
            ("Port", params.asia_port_cost),
            ("Other", params.other_cost),
        ]

    margin = price - sum(v for _, v in lines)
    return dict(revenue=price, lines=lines, margin=margin, duration_days=ledger.total_days)


def _physical_route_value(row: Mapping, params: model.Params, route: str) -> tuple[float, float]:
    """``(duration_days, full_cargo_value)`` from _physical_route_breakdown()
    -- see that function for what changes relative to model.strip() and
    why. full_cargo_value is margin (a $/MMBtu rate) scaled to the total
    loaded-cargo basis, matching _full_value()'s (legacy) return
    convention."""
    bd = _physical_route_breakdown(row, params, route)
    return bd["duration_days"], bd["margin"] * params.cargo_size


def physical_waterfall_breakdown(row: Mapping, params: model.Params) -> dict:
    """model.waterfall_breakdown()'s shape (per-route revenue/lines/margin
    in $/MMBtu) for app.py's waterfall/Sankey display, but physical-engine
    -derived (docs/PHASE2_PLAN.md Section 8 step 8) -- used by
    POST_LIFT_DIVERSION/PRE_LIFT_CARGO/VESSEL_PROGRAMME. RENEWAL_RATE_SCREEN
    keeps using model.waterfall_breakdown() unchanged (that mode never
    calls this)."""
    return {
        route: {k: v for k, v in _physical_route_breakdown(row, params, route).items()
                if k in {"revenue", "lines", "margin"}}
        for route in ("Europe", "Asia")
    }


def route_value(
    row: Mapping,
    params: model.Params,
    route: str,
    mode: DecisionMode,
    *,
    month_index: int,
    future_cargo: bool = False,
    first_cargo_state: FirstCargoState | None = None,
) -> RouteValue:
    """Value one route under an explicit decision state.

    RENEWAL_RATE_SCREEN keeps reading the legacy strip's ``eu_cargo``/
    ``asia_cargo`` full pre-lift cargo margins unchanged (Section 3: this
    mode's screening formula stays permanent). Every other mode
    (POST_LIFT_DIVERSION, PRE_LIFT_CARGO, VESSEL_PROGRAMME) is valued from
    the physical engine instead (Section 8 step 8) -- see
    _physical_route_value() for what changes and why.

    For an already loaded current cargo, procurement and loading are added
    back because they are sunk and cannot distinguish the remaining route
    alternatives. first_cargo_state (Section 3a) refines this for the
    *current* cargo only -- see cost_policy() for the three-state
    semantics; leave it None for future programme cargoes (future_cargo
    always wins regardless).
    """

    route = _normalise_route(route)
    mode = DecisionMode(mode)

    if mode == DecisionMode.RENEWAL_RATE_SCREEN:
        duration_days = _duration(row, route)
        full = _full_value(row, route)
    else:
        duration_days, full = _physical_route_value(row, params, route)

    proc_treatment = cost_policy(
        mode, "procurement", future_cargo=future_cargo, first_cargo_state=first_cargo_state
    )
    loading_treatment = cost_policy(
        mode, "loading", future_cargo=future_cargo, first_cargo_state=first_cargo_state
    )

    incremental = full
    if proc_treatment == CostTreatment.SUNK:
        incremental += float(row["proc"]) * params.cargo_size
    if loading_treatment == CostTreatment.SUNK:
        incremental += params.loading * params.cargo_size

    return RouteValue(
        route=route,
        month_index=month_index,
        load_month=pd.Timestamp(row["load_month"]),
        duration_days=duration_days,
        full_cargo_value=full,
        incremental_value=incremental,
        procurement_treatment=proc_treatment,
        loading_treatment=loading_treatment,
    )


def isolated_route_values(
    strip_df: pd.DataFrame,
    params: model.Params,
    mode: DecisionMode,
    *,
    month_index: int = 0,
    routes: Sequence[str] = ("Europe", "Asia"),
    first_cargo_state: FirstCargoState | None = None,
) -> tuple[RouteValue, ...]:
    if not 0 <= month_index < len(strip_df):
        raise IndexError("month_index outside strip")
    row = strip_df.iloc[month_index]
    return tuple(
        route_value(
            row, params, r, mode, month_index=month_index, future_cargo=False,
            first_cargo_state=first_cargo_state,
        )
        for r in routes
    )


def _representative_load_date(load_month: pd.Timestamp) -> pd.Timestamp:
    m = pd.Timestamp(load_month)
    return pd.Timestamp(year=m.year, month=m.month, day=15)


def _month_index_for_start(strip_df: pd.DataFrame, base_date: pd.Timestamp, start_day: float) -> int | None:
    future_date = base_date + pd.Timedelta(days=float(start_day))
    target = pd.Timestamp(year=future_date.year, month=future_date.month, day=1)
    matches = strip_df.index[pd.to_datetime(strip_df["load_month"]) == target]
    if len(matches) == 0:
        return None
    return int(matches[0])


def optimise_programme(
    strip_df: pd.DataFrame,
    params: model.Params,
    *,
    horizon_days: float,
    current_month_index: int = 0,
    current_mode: DecisionMode = DecisionMode.POST_LIFT_DIVERSION,
    current_first_cargo_state: FirstCargoState | None = None,
    max_additional_cargoes: int = 1,
    residual_value_per_day: float = 0.0,
    turnaround_days: float = 0.0,
    routes: Sequence[str] = ("Europe", "Asia"),
    route_feasible: Mapping[str, bool] | None = None,
) -> ProgrammeResult:
    """Enumerate deterministic one-vessel programmes on a continuous day grid.

    The current cargo is mandatory and valued under ``current_mode``
    (refined by ``current_first_cargo_state``, Section 3a -- applies only
    to this first leg). Every later cargo is optional and valued pre-lift
    using the strip row matching its representative start month (always
    ``future_cargo=True``, so ``current_first_cargo_state`` never applies
    to them, by design). A voyage is admitted only when the whole route
    fits inside ``horizon_days``; fractional cargoes are impossible.

    ``turnaround_days`` is the gap BETWEEN consecutive voyages (fixing the
    next cargo, positioning, waiting for the loading slot) -- inserted
    before every additional cargo, never before the first or after the
    last. At the 0.0 default the schedule is byte-identical to before this
    parameter existed. ``residual_days`` counts every non-sailing day in
    the horizon (turnaround gaps plus the end-of-horizon tail), all valued
    uniformly at ``residual_value_per_day`` -- so pricing idle hire with a
    negative rate covers the gaps too, not just the tail.
    """

    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    if max_additional_cargoes < 0:
        raise ValueError("max_additional_cargoes cannot be negative")
    if turnaround_days < 0:
        raise ValueError("turnaround_days cannot be negative")
    if not 0 <= current_month_index < len(strip_df):
        raise IndexError("current_month_index outside strip")

    normal_routes = tuple(_normalise_route(r) for r in routes)
    feasible = {r: True for r in normal_routes}
    if route_feasible:
        for key, value in route_feasible.items():
            feasible[_normalise_route(key)] = bool(value)

    base_row = strip_df.iloc[current_month_index]
    base_date = _representative_load_date(pd.Timestamp(base_row["load_month"]))
    plans: list[ProgrammePlan] = []
    tolerance = 1e-9

    def finish(legs: list[ProgrammeLeg], end_day: float) -> None:
        # Residual = every non-sailing day in the horizon: turnaround gaps
        # between voyages plus the end-of-horizon tail. At turnaround_days
        # == 0 this equals the old horizon - end_day exactly (legs butt).
        residual = max(float(horizon_days) - sum(leg.duration_days for leg in legs), 0.0)
        residual_value = residual * float(residual_value_per_day)
        total = sum(leg.value for leg in legs) + residual_value
        plans.append(
            ProgrammePlan(
                legs=tuple(legs),
                horizon_days=float(horizon_days),
                residual_days=residual,
                residual_value=residual_value,
                total_value=total,
            )
        )

    def extend(legs: list[ProgrammeLeg], end_day: float, additional_used: int) -> None:
        # Stopping is always a valid choice. This matters when the next cargo
        # has negative pre-lift value.
        finish(legs, end_day)
        if additional_used >= max_additional_cargoes:
            return

        next_start = end_day + float(turnaround_days)
        month_index = _month_index_for_start(strip_df, base_date, next_start)
        if month_index is None:
            return
        row = strip_df.iloc[month_index]
        for route in normal_routes:
            if not feasible.get(route, False):
                continue
            value = route_value(
                row,
                params,
                route,
                DecisionMode.PRE_LIFT_CARGO,
                month_index=month_index,
                future_cargo=True,
            )
            if next_start + value.duration_days > horizon_days + tolerance:
                continue
            leg = ProgrammeLeg(
                cargo_number=len(legs) + 1,
                route=route,
                month_index=month_index,
                load_month=value.load_month,
                start_day=next_start,
                duration_days=value.duration_days,
                value=value.incremental_value,
                decision_mode=DecisionMode.PRE_LIFT_CARGO,
            )
            extend(legs + [leg], leg.end_day, additional_used + 1)

    for route in normal_routes:
        if not feasible.get(route, False):
            continue
        current = route_value(
            base_row,
            params,
            route,
            current_mode,
            month_index=current_month_index,
            future_cargo=False,
            first_cargo_state=current_first_cargo_state,
        )
        if current.duration_days > horizon_days + tolerance:
            continue
        first_leg = ProgrammeLeg(
            cargo_number=1,
            route=route,
            month_index=current_month_index,
            load_month=current.load_month,
            start_day=0.0,
            duration_days=current.duration_days,
            value=current.incremental_value,
            decision_mode=DecisionMode(current_mode),
        )
        extend([first_leg], first_leg.end_day, 0)

    if not plans:
        raise ValueError("no feasible current-cargo route fits within the programme horizon")

    # Deduplicate identical sequences/end states created by recursion and sort
    # deterministically by value, then fewer residual days, then sequence.
    unique: dict[tuple, ProgrammePlan] = {}
    for plan in plans:
        key = tuple((leg.route, leg.month_index, round(leg.start_day, 9)) for leg in plan.legs)
        prior = unique.get(key)
        if prior is None or plan.total_value > prior.total_value:
            unique[key] = plan
    ranked = tuple(
        sorted(
            unique.values(),
            key=lambda p: (-p.total_value, p.residual_days, p.sequence),
        )
    )
    return ProgrammeResult(best=ranked[0], alternatives=ranked)


def programme_frame(result: ProgrammeResult) -> pd.DataFrame:
    rows = []
    for rank, plan in enumerate(result.alternatives, start=1):
        rows.append(
            {
                "rank": rank,
                "sequence": plan.sequence,
                "cargoes": len(plan.legs),
                "used_days": plan.used_days,
                "residual_days": plan.residual_days,
                "residual_value": plan.residual_value,
                "programme_value": plan.total_value,
            }
        )
    return pd.DataFrame(rows)


def schedule_frame(plan: ProgrammePlan) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "cargo": leg.cargo_number,
                "route": leg.route,
                "month_index": leg.month_index,
                "load_month": leg.load_month,
                "start_day": leg.start_day,
                "duration_days": leg.duration_days,
                "end_day": leg.end_day,
                "value": leg.value,
                "decision_mode": leg.decision_mode.value,
            }
            for leg in plan.legs
        ]
    )
