# Task 1 — Data-assembly layer of report_cohort.py, under tests (TDD)

## Goal
Make `src/DICOM_discovery/report_cohort.py` import cleanly and put its **pure data-assembly
layer under tests**. This is the spec's "tests cover the data-assembly layer" requirement
(Section C of docs/superpowers/specs/2026-06-17-dicom-discovery-v0.6-design.md).

You MUST follow Test-Driven Development (invoke the superpowers:test-driven-development skill):
write a failing test, run it, make it pass, refactor. Do not write implementation ahead of tests.

## Scope — IN
1. **Fix the import crash.** `report_cohort.py` currently raises `NameError: name 'json' is not
   defined` at import (line 772). Lines 770-772 are a dead, self-contradictory comment plus
   `_ = json`. `json` is used nowhere else in the file. **Delete lines 770-772.** Do NOT add
   `import json` — nothing uses it. After this, `python -c "import DICOM_discovery.report_cohort"`
   must succeed and `ruff check src synth tests` must be clean (this removes the only current error).
2. **Create `tests/test_report_cohort.py`** covering the SIX pure data-assembly functions:
   - `verdict_counts(rollup_df) -> Dict[str,int]` — must always return all 4 keys
     (OK, WARN, INCOMPLETE, NO_RT), defaulting absent ones to 0.
   - `cohort_pct_complete(comp_long) -> Optional[float]` — mean of per-patient pct_complete.
   - `build_kpis(rollup_df, comp_long) -> Dict` — keys: n_patients, verdicts, pct_complete.
   - `rollup_rows(rollup_df) -> List[dict]` — one dict per patient with chain + targets.
   - `study_findings(study_df) -> Dict[str, List[dict]]` — patient_id → per-study findings.
   - `completeness_rows(comp_long) -> List[dict]` — per-patient completeness summary.
   Tests must build a real cohort from fixtures and feed the actual frames through, NOT hand-mocked
   frames, so the tests pin real behaviour. Assert on returned dict/list structure and values —
   no HTML parsing (HTML is Task 2).
3. **Fix any data-assembly bugs** the tests reveal (these functions have never run). Keep changes
   minimal and inside the data-assembly functions; do NOT touch rendering functions (`_*_html`,
   `_styles`, `_script`, `_heatmap_html`) or `render_cohort_report` glue except as strictly needed
   to make the module import.

## Scope — OUT (do NOT do these — later tasks)
- No HTML/render tests, no `render_cohort_report` end-to-end test (Task 2).
- No CLI wiring / `report` subcommand in cli.py (Task 3).
- No frontend-design / visual changes (Task 2).
- No README/CHANGELOG (Task 3).

## How to build cohort frames in tests (from task0-recon.md — READ IT)
Use the existing session fixtures pattern in `tests/conftest.py`:
- `longitudinal` fixture → `(root, idx)`; then
  `state_df, hover_df, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)`
  and `rollup_df = build_rt_rollup(idx.table)`, `study_df = build_rt_integrity(idx.table)`.
- Known longitudinal truths you can assert against (from tests/test_completeness.py):
  L001 pct_complete == 100.0; L002 has a MISSING (M6/MR); L003 has a MISSING (baseline/RTDOSE).
- The `cohort` fixture (synthetic P001–P007) gives RT verdict variety for `verdict_counts` /
  `rollup_rows` / `study_findings`.
Exact interface signatures and return columns are in `docs/superpowers/sdd-tier2/task0-recon.md`
— treat that file as ground truth for the APIs.

## Constraints (non-negotiable, from spec)
- Python 3.8; env `/home/vmetzger/miniconda3/envs/epibrainrad/bin/python`.
- Header-only / RUO; do not break the indexer→table→pure-consumer boundary.
- All previously-passing tests stay green (40 today). `ruff check src synth tests` clean.

## Verification (run and paste real output into your report)
- `/home/vmetzger/miniconda3/envs/epibrainrad/bin/python -m pytest -q` → all green, report the count.
- `/home/vmetzger/miniconda3/envs/epibrainrad/bin/python -m ruff check src synth tests` → clean.
- `/home/vmetzger/miniconda3/envs/epibrainrad/bin/python -c "import DICOM_discovery.report_cohort"` → no error.
GOTCHA: never run overlapping background pytest here (NFS-latent repo); run one foreground.

## Commit
One atomic commit on branch `tier2-cohort-report`. Message style = existing history
(`git log --oneline`), imperative, scoped, with footer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
Include the (now import-clean) report_cohort.py + the new test file. Do NOT commit any
`*.html/*.csv/*.dcm` or output dirs (.gitignore already blocks them).

## Done when
report_cohort.py imports; test_report_cohort.py covers all 6 assembly functions and passes;
full suite green; ruff clean; one atomic commit made.
