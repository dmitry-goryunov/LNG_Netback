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

import model


class DecisionMode(str, Enum):
    """Economic state selected before a recommendation is calculated."""

    RENEWAL_RATE_SCREEN = "renewal_rate_screen"
    POST_LIFT_DIVERSION = "post_lift_diversion"
    PRE_LIFT_CARGO = "pre_lift_cargo"
    VESSEL_PROGRAMME = "vessel_programme"


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


def cost_policy(mode: DecisionMode, cost_type: str, *, future_cargo: bool = False) -> CostTreatment:
    """Return the decision-state treatment for one cost category.

    Future cargoes in a vessel programme are new pre-lift decisions and
    therefore include procurement and loading.  The currently loaded cargo in
    POST_LIFT_DIVERSION or VESSEL_PROGRAMME treats them as sunk.
    """

    try:
        mode = DecisionMode(mode)
    except ValueError as exc:  # pragma: no cover - defensive API validation
        raise ValueError(f"unknown decision mode {mode!r}") from exc

    cost_type = cost_type.lower().strip()
    if cost_type not in _COST_TYPES:
        raise ValueError(f"unknown cost type {cost_type!r}")

    if cost_type in {"procurement", "loading"}:
        if future_cargo:
            return CostTreatment.INCLUDED
        if mode in {DecisionMode.POST_LIFT_DIVERSION, DecisionMode.VESSEL_PROGRAMME}:
            return CostTreatment.SUNK
        return CostTreatment.INCLUDED

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


def route_value(
    row: Mapping,
    params: model.Params,
    route: str,
    mode: DecisionMode,
    *,
    month_index: int,
    future_cargo: bool = False,
) -> RouteValue:
    """Value one route under an explicit decision state.

    The legacy strip's ``eu_cargo``/``asia_cargo`` values are full pre-lift
    cargo margins.  For an already loaded current cargo, procurement and
    loading are added back because they are sunk and cannot distinguish the
    remaining route alternatives.
    """

    route = _normalise_route(route)
    mode = DecisionMode(mode)
    full = _full_value(row, route)

    proc_treatment = cost_policy(mode, "procurement", future_cargo=future_cargo)
    loading_treatment = cost_policy(mode, "loading", future_cargo=future_cargo)

    incremental = full
    if proc_treatment == CostTreatment.SUNK:
        incremental += float(row["proc"]) * params.cargo_size
    if loading_treatment == CostTreatment.SUNK:
        incremental += params.loading * params.cargo_size

    return RouteValue(
        route=route,
        month_index=month_index,
        load_month=pd.Timestamp(row["load_month"]),
        duration_days=_duration(row, route),
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
) -> tuple[RouteValue, ...]:
    if not 0 <= month_index < len(strip_df):
        raise IndexError("month_index outside strip")
    row = strip_df.iloc[month_index]
    return tuple(
        route_value(row, params, r, mode, month_index=month_index, future_cargo=False)
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
    max_additional_cargoes: int = 1,
    residual_value_per_day: float = 0.0,
    routes: Sequence[str] = ("Europe", "Asia"),
    route_feasible: Mapping[str, bool] | None = None,
) -> ProgrammeResult:
    """Enumerate deterministic one-vessel programmes on a continuous day grid.

    The current cargo is mandatory and valued under ``current_mode``.  Every
    later cargo is optional and valued pre-lift using the strip row matching
    its representative start month.  A voyage is admitted only when the whole
    route fits inside ``horizon_days``; fractional cargoes are impossible.
    """

    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    if max_additional_cargoes < 0:
        raise ValueError("max_additional_cargoes cannot be negative")
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
        residual = max(float(horizon_days) - end_day, 0.0)
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

        month_index = _month_index_for_start(strip_df, base_date, end_day)
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
            if end_day + value.duration_days > horizon_days + tolerance:
                continue
            leg = ProgrammeLeg(
                cargo_number=len(legs) + 1,
                route=route,
                month_index=month_index,
                load_month=value.load_month,
                start_day=end_day,
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
