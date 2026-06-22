# DICOM_discovery v0.6 — Design

**Date:** 2026-06-17
**Status:** Approved, implementation in progress
**Scope:** One work round, three independent sub-projects, sequenced Tier 0 → Tier 1 → Tier 2.

## Context & constraints

`DICOM_discovery` is a portfolio package: cohort-level QC for longitudinal radiotherapy
(DICOM-RT) data. A single `indexer.py` walks any tree → one canonical table; all analyses
(`rt_integrity.py`, `completeness.py`) are pure consumers of that table. This boundary is
the thing that makes the tool adaptive and **must not be broken**.

Non-negotiables (carried from the generalization council):
- **Do not** introduce directory/series sampling — it could silently miss RT objects.
- **Do not** break the indexer → table → pure-consumer boundary.
- Header-only reads (`stop_before_pixels=True`); no pixel data.
- **RUO only** — never claim clinical/diagnostic use.
- Reports refuse to emit on an empty/misunderstood scan (`--dry-run` preflight).
- Self-contained HTML output (inline assets, no CDN) — must open on an air-gapped network.
- No PHI in git history; `.gitignore` already blocks `*.dcm/*.csv/*.html` + output dirs.

Environment: Python 3.8, env `/home/vmetzger/miniconda3/envs/epibrainrad/bin/python`.
Tests today: 23 passing; `ruff check src synth tests` clean. Version 0.5.0 → 0.6.0.
Real cohort for benchmarking is reachable at
`/mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM` (~1.03M files).

---

## Section A — Tier 0: git + CI (foundation)

**Goal:** put the package under version control with an honest history, and add CI.

- `git init` in the package directory.
- **One honest initial commit** capturing the current v0.5.0 state. No fabricated
  multi-commit history; real atomic commits follow for the v0.6 work.
- Extend `.gitignore` if needed: `__pycache__/`, `*.egg-info/`, `.ruff_cache/`,
  `.pytest_cache/` (most already present — verify).
- `.github/workflows/ci.yml`: on push/PR, set up Python 3.8, `pip install -e ".[dev]"`,
  run `ruff check src synth tests` then `pytest`. Self-contained — the test suite generates
  synthetic data, so **no PHI ever reaches CI**.
- Local-only for now (no `gh` CLI installed); pushing to a remote is a later finishing step.

**Done when:** repo initialized, initial commit present, CI workflow committed, `.gitignore`
verified PHI-safe.

---

## Section B — Tier 1: faster traversal (headline)

**Goal:** cut wall-time / round-trips on the NFS-bound 1M-file scan, with a measured
before/after. All changes live in `indexer.py` and are **behaviour-preserving** (identical
canonical table; same content-detection semantics; no directory sampling).

### Changes
1. **One open per file** (the dominant NFS win). Today a non-trusted-ext file can be opened
   up to 3 times: `_has_dicm_preamble` (open #1) → `os.path.getsize` (stat) → `dcmread`
   (open #2) → and on the unreadable path `_index_one` calls `_has_dicm_preamble` *again*
   (open #3, `indexer.py:213`). Refactor so the file is opened once: read the 132-byte
   preamble, `seek(0)`, and hand the **same** file handle to `dcmread`. Propagate the
   preamble result out of `read_instance` so `_index_one` never re-opens.
2. **`specific_tags=`** on `dcmread`: restrict parsing to the ~15 tags the canonical record
   uses. Must include the sequence tags whose contents are read:
   `SOPClassUID, Modality, PatientID, StudyInstanceUID, StudyDate, SeriesInstanceUID,
   SOPInstanceUID, FrameOfReferenceUID, ReferencedFrameOfReferenceSequence,
   StructureSetROISequence, ReferencedStructureSetSequence, FractionGroupSequence,
   DoseUnits, DoseSummationType, ReferencedRTPlanSequence`. Verify every downstream field
   (ROI names, ref UIDs, `n_fractions`) is still populated identically.
3. **`--assume-immutable` warm cache** (opt-in flag, default off). When set with an existing
   `--cache`, skip the per-file `os.stat` (`indexer.py:261`) and key the cache by path only.
   New files (not in cache) are still read; **modified files are NOT re-read** — documented
   honestly as "for immutable/append-only archives only". Turns a warm re-scan from 1M stats
   into a readdir walk.
4. **Incremental checkpoint.** Flush the cache every N files (e.g. 5000) instead of only at
   the end (`indexer.py:286`), so a long or crashed scan resumes (the 18-min IC 034 case).
5. **Streaming `scandir` walk + live ETA** (UX + memory; smaller win). Stream paths into the
   executor rather than materializing all 1M up front.

Concurrency stays thread-based (latency-bound; GIL released during I/O). Any `--workers`
default change must be justified by the benchmark, not guessed.

### Benchmark (deliverable)
- A `bench/` harness (dev tooling, not shipped in the package import path) timing
  **cold vs warm × before vs after** on a **bounded NAS subset** (2–3 `IC` patients,
  few-thousand files) for a fast loop, plus **one full-cohort run** for the headline
  files/s and wall-time. Emits a markdown table. Prints only counts/timings — **no patient
  data**. The full-cohort run may be executed manually/in background (it is ~15–30 min).

### Tests (TDD)
- Extensionless DICOM still detected with the single-open path.
- `specific_tags` still yields RTSTRUCT `roi_names`, RTPLAN/RTDOSE referenced UIDs, and
  `n_fractions` — assert canonical table equality against the pre-change behaviour on the
  synthetic cohort.
- `--assume-immutable`: reuses cached records without calling `os.stat`, yet a newly added
  file is still picked up (and a modified file is intentionally not re-read).
- All 23 existing tests stay green; `ruff` stays clean.

**Done when:** changes implemented behind tests, suite green, ruff clean, benchmark table
produced (subset + at least planned full run), CHANGELOG updated.

---

## Section C — Tier 2: unified cohort report

**Goal:** one self-contained HTML report combining RT integrity and completeness, built with
the `frontend-design` skill for the visual layer.

- New module `report_cohort.py` and CLI command
  `dicom-discovery report --root … --out cohort_report.html [--protocol …]`: index once,
  feed `build_rt_rollup` + `build_rt_integrity` + `build_completeness`, render one HTML.
- **Self-contained**: inline Plotly + vanilla JS (no CDN), matching `report_map.py`.
  Air-gapped-openable.
- Layout (frontend-design refines aesthetics; semantics fixed here):
  - **Topbar**: tool name, RUO disclaimer, run-manifest summary (root, n_files,
    n_patients, n_studies, generated_utc).
  - **KPI cards**: patient verdict counts (OK / WARN / INCOMPLETE / NO_RT), cohort %
    complete.
  - **Tab 1 — RT Integrity**: per-patient verdict table (sortable, filterable, CSV-export),
    drill-down to per-study findings with severity/confidence badges.
  - **Tab 2 — Completeness**: the existing heatmap + per-patient completeness table.
- Preserve colour semantics: the 5 completeness states and the OK/WARN/INCOMPLETE severity
  scale must stay meaningful and distinct.
- Respects the `--dry-run` preflight (refuses to emit on an empty scan).
- Demo on the synthetic cohort; validate at scale against a real cached scan.

**Done when:** `report` command renders a self-contained HTML on the synthetic cohort,
preflight respected, frontend-design pass applied, tests cover the data-assembly layer,
suite green, ruff clean, README + CHANGELOG updated.

---

## Out of scope (deliberately deferred — council)
- Generic connected-components UID-graph chain reconstruction.
- PACS/QIDO second backend.
- Configurable path-template engine.
- Directory/series sampling for speed (would risk missing RT objects).
- TG-263 ROI nomenclature (candidate for a later round).

## Sequencing & integration
Tier 0 first (establishes the repo). Tier 1 and Tier 2 are logically independent but both
touch `cli.py`; implement sequentially (Tier 1 → Tier 2) to avoid merge conflicts, with the
test suite green and an atomic commit between tiers.
