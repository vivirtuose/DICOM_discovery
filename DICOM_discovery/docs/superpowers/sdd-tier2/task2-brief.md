# Task 2 — Self-contained HTML rendering, tested + frontend-design pass

## Goal
Make `render_cohort_report(...)` in `src/DICOM_discovery/report_cohort.py` produce ONE
self-contained, air-gapped-openable HTML report on the synthetic/longitudinal cohort, put it
under tests, and apply a `frontend-design` visual pass. (Spec Section C of
docs/superpowers/specs/2026-06-17-dicom-discovery-v0.6-design.md.)

TDD: write the failing test first (call render_cohort_report → read file → assert), run, then
fix rendering until green. Then do the frontend-design polish, re-running the test after.

## Prereqs you can rely on (do NOT redo)
- Task 1 already: fixed the import bug, and put the 6 pure data-assembly functions under tests
  (tests/test_report_cohort.py). Do not duplicate those tests.
- Exact interfaces + the air-gapped Plotly pattern are in docs/superpowers/sdd-tier2/task0-recon.md.
  READ IT. Key: report_map.py uses `fig.to_html(full_html=False, include_plotlyjs=True)` to embed
  plotly.js inline (no CDN). report_cohort.py `_heatmap_html` already does the same.
- `render_cohort_report(rt_study_df, rollup_df, comp_state, comp_hover, comp_long, manifest,
  protocol, out_path) -> str` is the single entry point (it calls the assembly + rendering fns,
  writes the HTML, returns the path). Read the function for the exact `manifest` dict keys it needs
  for the topbar (root, n_files, n_patients, n_studies, generated_utc — confirm against the code).

## Scope — IN
1. **Create the end-to-end render test(s)** in `tests/test_report_cohort.py` (append; keep Task 1
   tests). Build the longitudinal cohort via the existing fixture, compute the frames
   (build_rt_integrity, build_rt_rollup, build_completeness), call render_cohort_report to a
   `tmp_path` file, read it back, and assert ALL of:
   - **Self-contained / air-gapped** (mirror tests/test_completeness.py:84 `test_render_map_is_self_contained`):
     `'src="https://cdn.plot.ly' not in html` AND no other `src="http` external script/style refs;
     `len(html) > 1_000_000` (plotly embedded inline).
   - **Both tabs present**: an RT Integrity section/tab AND a Completeness section/tab (assert on
     stable tab labels/ids the rendering emits).
   - **KPI cards**: verdict counts (OK/WARN/INCOMPLETE/NO_RT) and cohort % complete appear.
   - **Topbar**: tool name + RUO disclaimer text + manifest summary (root, n_files, n_patients,
     n_studies, generated_utc) appear.
   - **Drill-down**: per-study findings for at least one patient appear in the RT table markup.
   - **Completeness heatmap** present (the embedded Plotly div) + per-patient completeness table.
2. **Fix rendering bugs** the test reveals (render_cohort_report and the `_*_html`/`_styles`/
   `_script` helpers). The whole render path has never executed end-to-end — expect real bugs.
3. **frontend-design pass**: invoke the `frontend-design` skill and apply an intentional visual
   layer (typography, spacing, KPI card design, table/badge styling, tab affordance). This is a
   portfolio artifact for a cancer-centre internship — it should look deliberate, not templated.
   Work in `_styles()` / structure of the `_*_html` helpers. Re-run the test after.

## Semantics that MUST be preserved (non-negotiable — do not "improve" away)
- The **5 completeness CellState colours** stay distinct and meaningful (reuse `_STATE_COLORS` /
  `_discrete_colorscale` from report_map.py — do not invent a new palette for the heatmap).
- The **OK / WARN / INCOMPLETE / NO_RT** verdict scale stays visually distinct and meaningful
  (the verdict pill colours in VERDICT_COLORS).
- Severity (ERROR/WARNING/INFO) + confidence (HIGH/HEURISTIC) badges in the drill-down stay legible.
- **RUO only** — the RUO disclaimer must remain visible in the topbar; never imply clinical use.
- Self-contained: no CDN / external asset references introduced by the design pass (web fonts must
  be system fonts or inlined — do NOT add a Google Fonts <link>).
- Do NOT break the indexer→table→pure-consumer boundary; rendering consumes frames only.

## Scope — OUT (later task)
- CLI `report` subcommand in cli.py, `--dry-run`/preflight wiring (Task 3).
- README / CHANGELOG (Task 3).

## Constraints
- Python 3.8; env /home/vmetzger/miniconda3/envs/epibrainrad/bin/python.
- All tests stay green; `ruff check src synth tests` clean.
- GOTCHA: NFS-latent repo — never run overlapping/background pytest; one foreground run.
- Do NOT commit generated *.html/*.csv (.gitignore blocks them) — the test writes to tmp_path.

## Verification (paste real output into report)
- `python -m pytest -q` (all green, report count) ; `python -m ruff check src synth tests` (clean).
- In the report, paste the asserted self-containment check result and the final byte size of the
  rendered HTML, and note that you eyeballed the rendered file (or describe what you verified).

## Commit
One atomic commit on `tier2-cohort-report`, imperative message matching existing history, footer
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Source + tests only; no HTML artifacts.

## Done when
render_cohort_report emits a self-contained HTML on the cohort; the render test asserts
self-containment + both tabs + KPIs + topbar/RUO + drill-down + heatmap and passes; frontend-design
pass applied with semantics preserved; full suite green; ruff clean; one atomic commit.

## Report to
docs/superpowers/sdd-tier2/task2-report.md (full detail). Return only: status, commit range,
one-line test summary, concerns.
