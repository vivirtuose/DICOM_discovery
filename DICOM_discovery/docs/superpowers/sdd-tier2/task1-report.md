# Task 1 Report — Data-assembly layer of report_cohort.py (Tier 2)

**Date:** 2026-06-17
**Branch:** `tier2-cohort-report`
**Commit:** `5e6c1f7` — feat(report_cohort): fix import bug + add data-assembly tests (Tier 2 Task 1)
**Commit range:** `ab5763f..5e6c1f7`

---

## Files changed

| File | Change |
|---|---|
| `src/DICOM_discovery/report_cohort.py` | Deleted lines 770-772 (dead `_ = json` that caused NameError at import). No other changes. |
| `tests/test_report_cohort.py` | New file: 30 tests covering all 6 data-assembly functions. |

---

## TDD steps

### RED phase — Write failing tests first

Wrote `tests/test_report_cohort.py` before touching `report_cohort.py`.
The file imports `from DICOM_discovery.report_cohort import ...` which triggers the known showstopper.

Ran tests:
```
pytest tests/test_report_cohort.py -q
==================================== ERRORS ====================================
_________________ ERROR collecting tests/test_report_cohort.py _________________
tests/test_report_cohort.py:27: in <module>
    from DICOM_discovery.report_cohort import (  # noqa: E402
src/DICOM_discovery/report_cohort.py:772: in <module>
    _ = json
E   NameError: name 'json' is not defined
=========================== short test summary info ============================
ERROR tests/test_report_cohort.py - NameError: name 'json' is not defined
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

Test failed exactly as expected — import crash, not a test logic error. Confirmed correct RED.

### GREEN phase — Minimal fix

Deleted lines 770-772 from `report_cohort.py`:
```python
# Keep a reference so linters don't flag the json import if templating changes; the
# manifest is escaped inline above. (json retained for forward use / debugging.)
_ = json
```
These were a dead comment + undefined reference. `json` is imported nowhere and used nowhere else in the file.

Re-ran tests immediately after:
```
pytest tests/test_report_cohort.py -v
30 passed in 0.63s
```
All 30 tests passed on first run — the data-assembly functions were already correct (no bugs found in the six functions; they had never executed but were well-written). No further production code changes needed.

### REFACTOR phase — Clean up ruff issues

Ruff reported 3 issues in the new test file:
1. `F401` — unused `import pytest` (removed)
2. `I001` — import block order (auto-fixed with `ruff --fix`)
3. `B007` — unused loop variable `pid` in `test_findings_field_is_list_of_dicts` (renamed to `_pid`)

After fixes, ruff clean.

---

## What tests were written (30 total)

### `TestVerdictCounts` (4 tests)
- `test_always_returns_all_four_keys_on_none` — None input → 4 keys, all 0
- `test_always_returns_all_four_keys_on_empty` — empty DataFrame → 4 keys, all 0
- `test_counts_real_verdicts` — real cohort rollup sums match patient count
- `test_missing_verdict_defaults_to_zero` — all VERDICT_ORDER keys present in output

### `TestCohortPctComplete` (3 tests)
- `test_none_for_empty_df` — empty long_df → None
- `test_returns_float_for_longitudinal` — longitudinal fixture → float in [0,100]
- `test_l001_pulls_mean_toward_100` — L001 is 100% complete so mean > 0

### `TestBuildKpis` (5 tests)
- `test_keys_present` — keys: n_patients, verdicts, pct_complete
- `test_n_patients_matches_rollup` — n_patients == len(rollup_df)
- `test_verdicts_is_dict_with_all_keys` — all 4 verdict keys present
- `test_pct_complete_type` — float or None
- `test_empty_rollup_zero_patients` — empty rollup → n_patients == 0

### `TestRollupRows` (6 tests)
- `test_empty_df_returns_empty_list` — empty/None → []
- `test_one_dict_per_patient` — len(rows) == len(rollup_df)
- `test_required_keys_present` — 7 required keys in every row dict
- `test_chain_is_list_of_four_bools` — chain: list of 4 bool
- `test_targets_has_gtv_ctv_ptv` — targets keys == {GTV, CTV, PTV}
- `test_rt_status_is_string` — rt_status is always str

### `TestStudyFindings` (6 tests)
- `test_empty_df_returns_empty_dict` — empty/None → {}
- `test_keys_are_patient_ids` — study_findings keys == rollup patient_ids
- `test_each_value_is_list_of_dicts` — per-patient value is list of dict
- `test_study_entry_has_required_keys` — 4 required keys per study entry
- `test_findings_field_is_list_of_dicts` — findings parsed to list of {severity, confidence, text}
- `test_findings_parsed_correctly_for_formatted_entry` — unit test: manually-crafted findings string with 2 entries parses correctly (severity/confidence/text)

### `TestCompletenessRows` (6 tests)
- `test_empty_df_returns_empty_list` — empty long_df → []
- `test_one_dict_per_mappable_patient` — len(rows) == len(patient_completeness(long_df))
- `test_required_keys_present` — 5 required keys per row
- `test_l001_is_100_pct` — L001 row pct_complete == 100.0
- `test_l002_has_missing` — L002 n_missing >= 1 (M6/MR MISSING)
- `test_pct_complete_in_valid_range` — all pct_complete in [0, 100]

---

## Verification output

### `pytest -q`
```
......................................................................   [100%]
70 passed in 1.84s
```
(40 pre-existing + 30 new)

### `ruff check src synth tests`
```
All checks passed!
```

### `python -c "import DICOM_discovery.report_cohort"`
```
(no output — import succeeds silently)
```

---

## Commit

```
commit 5e6c1f7
feat(report_cohort): fix import bug + add data-assembly tests (Tier 2 Task 1)

Delete dead lines 770-772 (``_ = json`` with no import json) that caused
NameError on import. Add tests/test_report_cohort.py: 30 tests covering all
six pure data-assembly functions (verdict_counts, cohort_pct_complete,
build_kpis, rollup_rows, study_findings, completeness_rows). Suite: 70 passed.
ruff clean. Module now imports on Python 3.8.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

## Concerns

None. All data-assembly functions were already correct — the single bug was the import crash, not logic errors in the functions themselves. The six functions had well-guarded None/empty checks, correct column names matching the upstream interfaces, and the `study_findings` parser handles the `[SEVERITY/CONFIDENCE] CODE: message` format correctly.

The 30 tests pin real behaviour from actual cohort fixtures (no hand-mocked DataFrames), as required by the task brief.
