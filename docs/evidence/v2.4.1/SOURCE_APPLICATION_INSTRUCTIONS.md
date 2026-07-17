# Source application instructions

> **Editor's note (applied 17-Jul-2026):** archived here as evidence per the traceability register's evidence convention (`docs/evidence/<release>/...`). None of the three artefacts described below (`.zip`, `.patch`, `.bundle`) were actually applied — only the `.zip` was ever present on disk, and it was deleted after the underlying changes were reviewed and independently verified directly from the working tree. The change was applied via a plain commit to `main` (`103231006c43a37643b816481ab229535d7c6ca0`, tagged `v2.4.1-risk-equivalence`), not `git am` or `git clone` from a bundle. See `docs/LNG_NETBACK_PROGRESS_TRACEABILITY_2026-07-17.md` for the corrected status.

Release: v2.4.1-risk-equivalence

Final commit: 86ae95db057bbeee5fe82df840f77602b364d80a

Branch: feature/v2.4.1-risk-equivalence

Tag: v2.4.1-risk-equivalence

The validated release was built from the current Google Drive source snapshot and passed the full release gate. The Drive connector was able to create project documentation but was not authorised to overwrite the pre-existing raw Python and Markdown files. Apply one of the supplied artefacts to the source repository.

## Artefacts

`LNG_Netback_v2.4.1-risk-equivalence.zip`
SHA-256: `bd429fd538f539b10c9df203b81a87af1f7bdb207996b92fe1ccedf1008b36b4`
Use this as the complete updated source tree. The proprietary workbook is deliberately excluded.

`v2.4.1-risk-equivalence.patch`
SHA-256: `a4201fbc330a3be8ec4a01d2f0a92852de503515d629a408a945fb2ddd9e3830`
Apply from the existing project repository with: `git am v2.4.1-risk-equivalence.patch`

`v2.4.1-risk-equivalence.bundle`
SHA-256: `78cfbd5c9128e5a30994f9b7ae0669ac94deb85263f53eb77d8970efefcd67b1`
This contains `main`, `feature/v2.4.1-risk-equivalence` and the annotated tag. Example: `git clone v2.4.1-risk-equivalence.bundle LNG_Netback_v2.4.1`

## Patch validation

The patch was applied to a clean clone of the imported main snapshot and the targeted suite passed 10/10. The original release worktree passed 64/64 frozen legacy checks, 144/144 pytest checks and the five-page Streamlit smoke test.

## Files changed

- `risk.py`
- `tests/test_risk_containment.py`
- `README.md`
- `docs/IMPLEMENTATION_STATUS.md`
- `docs/RISK_EQUIVALENCE_FIX.md`
- `test_results/v2.4.1/*`

After applying the release, retain the existing `LNG history.xlsx` beside the source or set `LNG_HISTORY_XLSX`. Re-run the full validation commands recorded in `RISK_EQUIVALENCE_FIX`.
