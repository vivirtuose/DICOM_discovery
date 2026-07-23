# DICOM_discovery v0.8 — multi-env packaging + CI proof-of-work — Design

**Date:** 2026-07-23
**Status:** Approved (forks confirmed with maintainer), implementation in progress
**Branch:** `multi-env-packaging`
**Base:** v0.7.0 (137 tests green on a fresh Python 3.14 env; `ruff` clean)

## Goal

Three maintainer objectives, all RUO, all preserving the non-negotiables from the v0.6
design (indexer → canonical table → pure consumers; header-only reads; no PHI in git;
self-contained air-gapped HTML):

1. **Multi-Python-env compatible** — the package installs and passes its suite across a
   matrix of supported Python versions, not just 3.8.
2. **One-command install** — `pip install "git+https://github.com/vivirtuose/DICOM_discovery.git#subdirectory=DICOM_discovery"`
   yields a *working* tool (headline HTML report included) with no extra steps.
3. **Synthetic-data proof-of-work in GitHub** — CI generates an open synthetic cohort, runs
   the real `report` + verdict-`--json` pipeline on it, and uploads the artifacts, proving
   end-to-end health with zero PHI.

## Confirmed decisions (maintainer)

- **Python matrix:** 3.9–3.13 on Ubuntu. Drop EOL 3.8.
- **plotly:** promote from the optional `[viz]` extra to a **core dependency** so the report
  works from a bare install.
- **Proof-of-work depth:** CI **artifacts** (uploaded HTML + `verdicts.json`) + a status
  badge. No GitHub Pages.
- **Push:** maintainer pushes. This branch is prepared and verified locally only.

## The cross-version constraint that shapes the work

pydicom 3.0 requires Python ≥3.10, so on **Python 3.9 the matrix resolves pydicom 2.4.x**,
and on 3.10–3.13 it resolves pydicom 3.x. Any code in the synthetic generators must therefore
work on **both** pydicom 2.4 and 3.x. The modern write API (`enforce_file_format`,
`little_endian=`/`implicit_vr=` kwargs) is 3.0-only → **not usable** while 3.9 is supported.
Consequence: keep the generators on the widely-compatible write API and **silence the known
pydicom 4.0-removal DeprecationWarnings** via pytest `filterwarnings` rather than rewriting to
the 3.0-only API. Full pydicom-4.0 modernization is deferred to when 3.9 is dropped.

## Work items

### A — Packaging / dependency correctness
- `pyproject.toml`: `requires-python = ">=3.9"`; move `plotly>=5.0` into core `dependencies`;
  drop the now-redundant `[viz]` extra; `ruff target-version = "py39"`; bump `version` to
  `0.8.0`. `__init__.__version__ → 0.8.0`.
- **Already landed on this branch (commit 7ffc107):** PyYAML declared as a core dependency —
  `load_protocol()` imported `yaml` without declaring it, so a clean install failed on the
  protocol path (1 test failed on a fresh env).

### B — Forward-compat fixes (objective 1)
- `datetime.datetime.utcnow()` → timezone-aware UTC producing the **identical**
  `YYYY-MM-DDTHH:MM:SSZ` string, at `indexer.py:405` and `contract.py:104`. (`utcnow()` is
  deprecated and slated for removal; no test pins the exact format — contract only requires a
  string.)
- pytest `filterwarnings` in `[tool.pytest.ini_options]`: turn the deprecation *noise* (pydicom
  `is_little_endian` / `is_implicit_VR` / `write_like_original`) into a documented, quiet allow —
  keeping CI output clean without breaking the pydicom-2.4/py3.9 leg.

### C — CI matrix (objective 1)
- `.github/workflows/ci.yml`: `strategy.matrix.python-version: [3.9, 3.10, 3.11, 3.12, 3.13]`,
  `runs-on: ubuntu-latest`, `pip install -e ".[dev]"`, `ruff check src synth tests`, `pytest`.

### D — CI proof-of-work job (objective 3)
- A `proof` job (needs `test`): install the package, generate the longitudinal synthetic cohort
  (`python synth/generate_longitudinal_cohort.py --out-dir examples/longitudinal_cohort` — it
  exercises **both** report tabs: RT chain + follow-up MR), then run the real pipeline
  `dicom-discovery report --root examples/longitudinal_cohort --out cohort_report.html --json verdicts.json`,
  and `actions/upload-artifact` the HTML + JSON. Counts/timings only; **no PHI** ever exists —
  the data is synthesised in the job.

### E — Docs (objectives 2 & 3)
- `README.md`: a **Install** section with the one-command `pip install git+…#subdirectory=…`
  line; CI + "synthetic proof" badges; note plotly is now core (report works out of the box).
- `CHANGELOG.md`: a `0.8.0` entry.

## Done when
- `pytest` green and `ruff` clean **locally on this machine** (Python 3.14 stands in for the
  matrix here; the matrix itself is proven once pushed).
- The proof-of-work commands run locally and produce a self-contained `cohort_report.html`
  (>1 MB, no CDN) plus a schema-valid `verdicts.json`.
- README + CHANGELOG updated; branch left ready for the maintainer to push (CI then runs the
  matrix + uploads the proof artifacts).

## Out of scope (this round)
- pydicom-4.0 API migration (blocked by the 3.9 leg; revisit when 3.9 is dropped).
- GitHub Pages hosting of the demo report.
- PyPI publication (Git install is the one-command target for now).
- Windows/macOS CI legs (Ubuntu-only this round).
