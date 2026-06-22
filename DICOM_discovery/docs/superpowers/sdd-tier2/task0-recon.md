# Task 0 — Recon: report_cohort.py + interfaces (verified 2026-06-17)

## Showstopper (verified by running)
- `import DICOM_discovery.report_cohort` → `NameError: name 'json' is not defined` at **line 772** (`_ = json`).
- Lines 770-772 are a dead, self-contradictory comment + `_ = json` with NO `import json` anywhere. `json` is used nowhere else.
- **Fix:** delete lines 770-772. (Do NOT add `import json` — nothing uses it.)
- ruff: exactly 1 error today = F821 undefined `json` line 772.
- Existing suite: **40 tests pass**. No test imports report_cohort yet.

## report_cohort.py structure
DATA-ASSEMBLY (pure: DataFrame → dict/list, no HTML):
- `verdict_counts(rollup_df) -> Dict[str,int]` (line 69) — always returns all 4 VERDICT_ORDER keys, 0 default
- `cohort_pct_complete(comp_long) -> Optional[float]` (79) — mean pct_complete via patient_completeness
- `build_kpis(rollup_df, comp_long) -> Dict[str,object]` (87) — {n_patients, verdicts, pct_complete}
- `rollup_rows(rollup_df) -> List[dict]` (98) — per-patient rows w/ chain + targets
- `study_findings(study_df) -> Dict[str,List[dict]]` (117) — patient_id → per-study findings parsed from `findings` col
- `completeness_rows(comp_long) -> List[dict]` (145) — per-patient summary via patient_completeness

RENDERING (HTML strings): `_heatmap_html` (162, the Plotly embed), `_esc`, `_chain_strip_html`, `_verdict_pill`, `_targets_html`, `_topbar_html`, `_kpi_html`, `_rt_table_html` (267), `_comp_table_html` (343), `_legend_html`, `_styles` (397, CSS), `_script` (556, vanilla JS: tabs/filter/sort/CSV export).

GLUE / public entry:
- `render_cohort_report(rt_study_df, rollup_df, comp_state, comp_hover, comp_long, manifest, protocol, out_path) -> str` (703) — calls all assembly + rendering, writes HTML to out_path, returns str(out_path).

NOTE: report_cohort.py does NOT itself call build_rt_integrity / build_rt_rollup / build_completeness — the CLI handler must compute those and pass the frames in.

## Consumed interfaces (exact, from their own modules)
- `rt_integrity.build_rt_rollup(table: pd.DataFrame) -> pd.DataFrame` (rt_integrity.py:295). One row/patient. Cols: patient_id, n_studies, n_rt_studies, rt_status{OK,WARN,INCOMPLETE,NO_RT}, fragmented, has_CT, has_RTSTRUCT, has_RTDOSE, has_RTPLAN, roi_GTV, roi_CTV, roi_PTV, reason.
- `rt_integrity.build_rt_integrity(table: pd.DataFrame) -> pd.DataFrame` (rt_integrity.py:265). One row/(patient,study). rt_status also includes NOT_RT. Cols incl. study_date, findings, n_errors/n_warnings/n_info, roi_names, dose_units, n_fractions, frame_consistent, plan_links_struct, dose_links_plan.
- `completeness.build_completeness(table, protocol=DEFAULT_PROTOCOL) -> (state_df, hover_df, long_df)` (completeness.py:126). state_df int CellState matrix; long_df cols: patient, timepoint, modality, expected, observed, state.
- `cli._preflight(idx: IndexResult, protocol=None) -> bool` (cli.py:37). Prints diagnostics; returns True if idx.manifest["n_dicom_indexed"]>0.
- `completeness.patient_completeness(long_df) -> DataFrame` (completeness.py:191) — used by assembly fns.

## Air-gapped HTML pattern (must match)
report_map.py:99 → `fig.to_html(full_html=False, include_plotlyjs=True)` embeds full plotly.js inline (~3.5MB), no CDN src. report_cohort.py `_heatmap_html` (line ~189) already uses the same. Test `tests/test_completeness.py:84` (`test_render_map_is_self_contained`) asserts `'src="https://cdn.plot.ly' not in page` and `len(page) > 1_000_000` — copy this as the HTML self-containment template.

## CLI wiring pattern (add `report` like `completeness`)
- `c = sub.add_parser("completeness", ...)` ; `_common(c)` (attaches --root --group-by --patient-regex --workers --cache --assume-immutable --dry-run) ; `c.add_argument("--protocol")` ; `c.add_argument("--out", default=...)` ; `c.set_defaults(func=_cmd_completeness)`.
- Handler `_cmd_completeness(args) -> int` (cli.py:114): load protocol → build_index(...) → `ok = _preflight(idx, protocol)` → `if args.dry_run: return 0` → `if not ok: return 1` → analyse + write + print path → return 0.
- For `report`: handler computes build_rt_integrity + build_rt_rollup + build_completeness, then render_cohort_report(...). Default --out = cohort_report.html.

## Test conventions
- `tests/conftest.py` session fixtures: `cohort` (generate_synthetic_cohort → build_rt_integrity_from_dir) and `longitudinal` (generate_longitudinal_cohort → build_index). Use `tmp_path_factory`.
- Assembly fns testable directly on the frames (no HTML parsing). HTML test: call render_cohort_report, read file, assert string presence/absence (per test_completeness.py:84).
- Longitudinal cohort known truths (from test_completeness.py): L001 pct_complete==100.0; L002 M6/MR MISSING; L003 baseline/RTDOSE MISSING. Demo root `examples/longitudinal_cohort` exercises both tabs (RT chain + follow-up MR).
