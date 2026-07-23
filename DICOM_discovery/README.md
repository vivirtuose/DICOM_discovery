# DICOM_discovery

[![CI](https://github.com/vivirtuose/DICOM_discovery/actions/workflows/ci.yml/badge.svg)](https://github.com/vivirtuose/DICOM_discovery/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.9%E2%80%933.13-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Use: research-only](https://img.shields.io/badge/use-research--only-orange)

**Cohort-level quality control for longitudinal radiotherapy (DICOM-RT) data.**

Before any radiomics or dose–outcome study can run, someone has to prove the cohort is
actually usable: that every patient has a complete, internally consistent RT chain
(`CT → RTSTRUCT → RTPLAN → RTDOSE`), sharing one frame of reference, with the target
volumes contoured. On a real clinical PACS this is rarely true — exports are duplicated,
references dangle, frames of reference drift. This tool finds those problems **at the
cohort level** and explains each one, so they are caught *before* they silently corrupt a
study (e.g. a patient analysed against the wrong structure set).

This is not a DICOM viewer and not a PACS. It is the unglamorous data-curation step that
precedes modelling — deliberately, because that step is where clinical cohorts break.

## Architecture — why it adapts to any server

A single **indexer** walks any file tree and reads DICOM *headers* into one canonical
table (`patient · study · series · modality · FrameOfReferenceUID · referenced UIDs ·
path`). Every analysis (RT integrity, completeness) consumes **only that table** and never
touches the filesystem. So the file path stops being *data* and becomes an opaque pointer:
patient/study/timepoint identity comes from DICOM tags, not folder positions, and the tool
works the same on a tidy export, a flat dump, or a different hospital's layout. DICOM files
are detected by **content** (the `DICM` preamble / a parseable SOPClassUID), not by a
`.dcm` extension — real exports are often extensionless.

---

## Install

Runs on **Python 3.9–3.13** (Linux / macOS / Windows). One command, straight from GitHub —
no clone needed (the package lives in the `DICOM_discovery/` subdirectory of the repo):

```bash
pip install "git+https://github.com/vivirtuose/DICOM_discovery.git#subdirectory=DICOM_discovery"

dicom-discovery --help
```

The interactive Plotly report is a **core dependency**, so `dicom-discovery report` works out
of the box — there is no separate visualisation step to install. For development, clone the
repo and use an editable install with the test extras (`pip install -e ".[dev]"`).

---

## Quickstart

```bash
pip install -e ".[dev]"

# One command: generate synthetic cohorts, run both engines, write the completeness map.
dicom-discovery demo                       # outputs land in ./examples/
xdg-open examples/completeness_map.html

# Point the tools at any DICOM tree (recurses; reads headers only):
dicom-discovery index        --root /data/COHORT --dry-run          # preflight: what's there?
dicom-discovery rt-check     --root /data/COHORT --out-csv rt.csv   # + rt_by_patient.csv
dicom-discovery completeness --root /data/COHORT --out map.html

# Unified RT-integrity + completeness report in one self-contained HTML (RUO; opens air-gapped):
dicom-discovery report       --root /data/COHORT --out cohort_report.html
dicom-discovery report       --root /data/COHORT --dry-run           # preflight only, no output

# Emit the verdict as a versioned, schema-validated artifact (the HTML is one projection of it):
dicom-discovery report       --root /data/COHORT --out cohort_report.html --json verdicts.json
dicom-discovery rt-check     --root /data/COHORT --json verdicts.json

pytest                                     # run the test suite
```

### The verdict contract (`--json`)

`--json` writes a `verdicts.json` validated against a versioned JSON Schema
(`schema_version`). It carries the tool version, a UTC timestamp, the protocol, a **run
manifest with a content-hash provenance fingerprint**, and one entry per patient
(`verdict`, `reason`, recommended `action`, chain/ROI flags, TG-263 `n_roi_nonstandard`).
This is what makes the output a *reusable, re-runnable, diffable* artifact rather than a
report to read by eye — a downstream pipeline consumes the JSON; the HTML is one projection
of the same payload. **RUO — not for diagnostic use.**

ROI names are checked against a bounded subset of **AAPM TG-263** nomenclature (Mayo et al.,
2018) as an additive signal; it never changes the OK/WARN/INCOMPLETE verdict. Roadmap:
provenance down to the SOPInstanceUID, DICOM-SR (PS3.16) / FHIR export, IHE-RO alignment.

`--group-by dicom` (default) keys patients by the `PatientID` tag; `--group-by folder`
keys by the top folder (use it when `PatientID` is a hospital MRN rather than the study id).
`--dry-run` runs a preflight that reports what was found and **refuses to emit a report on
an empty scan** — so the tool never produces a falsely-confident result on a tree it did
not understand. `dicom-discovery` ≡ `python -m DICOM_discovery`.

---

## What it catches — a real example

The QC grades every patient `OK` / `WARN` / `INCOMPLETE` and attaches *explainable*
findings, each with a **severity** (`ERROR`/`WARNING`/`INFO`) and a **confidence**
(`HIGH` for facts derived from referenced UIDs, `HEURISTIC` for guesses). The headline
case — the kind of bug that would otherwise pass unnoticed into a study:

```
P003  WARN  [ERROR/HIGH] PLAN_STRUCT_LINK_BROKEN:
            RTPLAN references a RTSTRUCT SOPInstanceUID absent from this patient
```

That patient *looks* complete (it has a CT, an RTSTRUCT, an RTPLAN and an RTDOSE), but the
plan was contoured against a structure set that is no longer in the folder. A naive file
count says "fine"; the dosimetry is not.

## Validation: case → expected → detected

The synthetic cohort carries its own ground truth, so the QC is validated end-to-end
(this table is produced by `pytest` and the demo above):

| Patient | Scenario | Expected | Detected |
|---------|----------|----------|----------|
| P001 | Complete chain, consistent FoR, all targets | `OK` | `OK` ✅ |
| P002 | Two RTSTRUCT re-exports, plan links the 2nd | `OK` | `OK` ✅ |
| P003 | RTPLAN references an **absent** RTSTRUCT | `WARN` (`PLAN_STRUCT_LINK_BROKEN`) | ✅ |
| P004 | Orphan RTDOSE → absent RTPLAN | `WARN` (`DOSE_PLAN_LINK_BROKEN`) | ✅ |
| P005 | RTDOSE FrameOfReference ≠ RTSTRUCT/RTPLAN | `WARN` (`FOR_INCONSISTENT_RT`) | ✅ |
| P006 | No RTDOSE | `INCOMPLETE` (`MISSING_RTDOSE`) | ✅ |
| P007 | Complete chain but no PTV | `WARN` (`MISSING_TARGET_ROI`) | ✅ |

## On a real cohort (98 patients, ~1M files)

Run against a real brain-RT trial export, the tool surfaced things a naive scan hides:

- The tree holds **1,028,034 files** — only ~47k carry a `.dcm` extension; **~970k are
  extensionless DICOM** (`image (0128)` …). The earlier extension-filtered scan was blind
  to ~95 % of the imaging; content-detection recovers it.
- The DICOM `PatientID` tag is the **hospital MRN**, not the study pseudonym `IC NNN`
  (the folder). The `--dry-run` preflight flags this *before* any report, so you switch to
  `--group-by folder` rather than silently keying patients by MRN.
- Patients have **7.5 studies on average** (longitudinal MR follow-up). Only **102 of 731
  studies actually contain an RT object**; the other 629 are follow-up MR and are reported
  as `NOT_RT` (out of scope) instead of being wrongly flagged "incomplete RT chain".
- **Per-patient verdict** (the unit a PI cares about): **96 OK / 3 WARN / 3 INCOMPLETE**
  over 97 patients with RT; 1 patient is imaging-only (`NO_RT`). One patient's RT chain is
  *fragmented* across several studies — reported as a single honest verdict
  ("`INCOMPLETE: missing RTDOSE; FRAGMENTED across N studies`"), not as confusing
  per-study noise, by reconciling the chain through resolved referenced UIDs.

## Longitudinal completeness — observed vs. expected

"Complete" means nothing without a protocol to compare against. Given a `Protocol`
(timepoints + the modalities required at each — editable as YAML, see
`protocol.brain_rt_followup.yaml`), every `(patient, timepoint, modality)` cell is graded
and rendered as a self-contained heatmap whose colours make the one distinction a file
count cannot:

| Colour | State | Meaning |
|--------|-------|---------|
| 🟩 green | `PRESENT` | expected and found |
| 🟥 red | `MISSING` | **expected but absent** — the actionable cell |
| 🟦 blue | `EXTRA` | found but not in the protocol |
| ⬜ grey | `N/A` | not expected (an empty cell that is *fine*) |

An empty-but-expected cell (red) is visually distinct from a merely-irrelevant cell
(grey): that separation is the whole point. The map embeds Plotly inline (no CDN, opens on
an air-gapped clinical network) and is accompanied by a per-patient completeness table:

```
patient  n_expected  n_present  n_missing  pct_complete
   L001           8          8          0         100.0
   L002           8          7          1          87.5   # follow-up gap (no M6 MR)
   L003           8          6          2          75.0   # no baseline RTDOSE; no M12 MR
   L004           8          5          3          62.5   # baseline only, lost to follow-up
```

## What changed vs the previous version (and why)

The earlier prototype inspected a *single* object per modality (`df.iloc[0]`), which
produced two documented false positives:

- **Multi-export RTSTRUCT** (P002): a patient with several structure-set re-exports was
  wrongly flagged "RTPLAN does not reference the RTSTRUCT" whenever the plan pointed at a
  different export. v2 resolves the link against the **set of all present**
  SOPInstanceUIDs, so P002 is correctly `OK`. (`tests/test_rt_integrity.py::test_p002_no_multi_struct_false_positive`
  is a regression test for exactly this.)
- **Planning-CT guess**: the CT/FoR check used `"RT" in filepath`, yielding spurious "FoR
  RT ≠ FoR CT". v2 keeps this check but downgrades it to a `HEURISTIC`/`INFO` hint that
  never, on its own, fails a patient — because *which* CT is the planning CT is genuinely
  uncertain from headers alone.

The point: a QC that cries wolf is worse than no QC. Findings are now graded so a reader
can separate a hard chain break (`ERROR`/`HIGH`) from a low-confidence hint.

Later, running at full cohort scale surfaced two more: integrity is assessed **per study**
(so a re-plan with its own frame of reference is not a false inconsistency), studies with
no RT object are scoped out as `NOT_RT`, and a **per-patient rollup** reconciles the chain
across studies via resolved referenced UIDs — distinguishing a genuinely missing link from
a merely *fragmented* one, without the naive object-union that would produce a falsely
reassuring `OK`.

## Known limitations

This is a research data-curation tool (**Research Use Only**), not a medical device and
not a clinical safety check.

- The planning-CT ↔ RT frame-of-reference match is heuristic (`INFO` only).
- The per-patient rollup reconciles the chain through *resolved* referenced UIDs but does
  not build a general connected-components graph; a deeply fragmented chain is flagged
  `FRAGMENTED` for manual review rather than fully reconstructed. (Deliberate scope choice.)
- Scanning is header-only but reads every file once; on a ~1M-file tree over NFS this takes
  a while (no persistent index cache yet — a natural next step).
- Validated on one centre's cohort and a synthetic set; other vendors' exports (private
  tags, transfer syntaxes) are not yet characterised.
- ROI target detection is substring-based (`GTV`/`CTV`/`PTV`); no TG-263 nomenclature yet.

## Data & privacy

No real patient data is in this repository. The example cohort is fully synthetic
(fabricated UIDs, `SYNTHETIC^PHANTOM` as patient name, header-only, no pixel data) and is
regenerated on demand. The `.gitignore` blocks `*.dcm`, `*.csv`, `*.html` and output
folders so that real clinical data and generated reports are never committed.

## Layout

```
src/DICOM_discovery/
    indexer.py           # walk any tree -> canonical DICOM table (content-detected, traced keys)
    rt_integrity.py      # RT chain integrity (per study) + per-patient rollup
    completeness.py      # observed-vs-expected model (Protocol, timepoint from StudyDate)
    report_map.py        # self-contained completeness heatmap (Plotly embedded, no CDN)
    report_cohort.py     # unified RT-integrity + completeness cohort report (self-contained HTML)
    cli.py / __main__.py # `dicom-discovery` commands: demo / index / rt-check / completeness / report
    synthetic.py         # synthetic DICOM-RT + longitudinal cohorts (+ ground truth)
protocol.brain_rt_followup.yaml       # example expected-content protocol
tests/                                # pytest suite (140 tests), driven by the synthetic cohorts
```
