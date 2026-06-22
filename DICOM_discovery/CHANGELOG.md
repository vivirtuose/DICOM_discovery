# Changelog

All notable changes to **DICOM_discovery**. The package began as a single-institution RT
data-curation script and was rebuilt, in council-reviewed increments, into an adaptive
cohort-QC tool.

## 0.7.0 — versioned verdict contract, actionable registry, TG-263 (council 2026-06-18)
Decided by LLM council + maintainer. Reframes the output from "a package that emits HTML"
toward a tool whose verdict is a reusable, reproducible artifact; the HTML report becomes a
*projection* of that artifact. RUO throughout.
- **Versioned verdict contract** (`contract.py`): `build_verdict_payload()` assembles a
  JSON-Schema-validated payload (`schema_version`, `tool_version`, `generated_utc`, protocol,
  a `run` manifest with a content-hash provenance fingerprint, and one entry per patient).
  `dicom-discovery report --json verdicts.json` and `rt-check --json` emit it; the document is
  re-runnable, diffable and auditable without parsing HTML. Adds a `jsonschema` dependency.
- **Actionable registry**: each patient verdict now carries a recommended `action` (fetch from
  PACS, verify the reference link, retrieve the planning CT, check the FrameOfReference, contour
  the missing target, regroup a fragmented chain). `order_for_review()` orders the rollup as a
  triage queue — INCOMPLETE then WARN float to the top.
- **Light AAPM TG-263 check** (`tg263.py`, Mayo et al. 2018): flags off-nomenclature ROI names
  (`n_roi_nonstandard` / `roi_nonstandard`) as an *additive* signal — the OK/WARN/INCOMPLETE
  verdict scale is unchanged. Bounded brain-RT subset.
- **Roadmap**: richer provenance to the SOPInstanceUID, DICOM-SR (PS3.16) / FHIR export of
  findings, IHE-RO alignment, and verdict validation against a manual ground truth.
- Version unified to 0.7.0 across `pyproject.toml` and `__init__` (corrects a stale `__version__`).

## 0.6.0 — faster NFS traversal + unified cohort report (Tier 2)
All changes live in the indexer and preserve the canonical table exactly (proven by a
table-equality test on the synthetic cohort, including the extensionless force-parse path).
- **`report` command**: `dicom-discovery report --root <dir> --out cohort_report.html` produces
  a single self-contained HTML combining the RT-integrity chain check and the observed-vs-expected
  completeness heatmap. Plotly is embedded inline (no CDN, opens air-gapped). Preflight/`--dry-run`
  contract preserved (refuses empty scan; dry-run writes nothing). RUO. 87 tests; `ruff` clean.
- **One open per candidate file** (the dominant slow-share win). The 132-byte `DICM`
  preamble is sniffed from the same handle that is then `seek(0)`-ed and handed to
  `dcmread` — no second open, and the size guard reads `os.fstat` on the open handle
  instead of a separate `os.path.getsize`. The unreadable path no longer re-opens to
  re-sniff: `read_instance` propagates the preamble result to the caller.
- **`specific_tags=` on `dcmread`**: only the ~15 tags the canonical record uses (incl. the
  RT sequences whose contents are read) are parsed, cutting bytes/CPU. A regression test
  asserts every nested field (ROI names, referenced struct/plan UIDs, `n_fractions`, dose
  fields, frame-of-reference) is still populated identically on both the `.dcm` and the
  extensionless paths.
- **`--assume-immutable` warm-cache fast path** (opt-in, default off): with `--cache`, skips
  the per-file `os.stat` and keys the cache by path alone. New files are still read; files
  modified in place are intentionally NOT re-read — documented as *for immutable/append-only
  archives only*. Without the flag, behaviour is exactly as before (stat-based invalidation).
- **Incremental cache checkpoint**: the cache is flushed every N files read (default 5000)
  in addition to the final write, so a long or crashed scan resumes.
- **Streaming `os.walk` traversal**: paths are streamed into the thread pool instead of
  materializing the full ~1M-path list first, so header reads begin immediately.
- `bench/` dev harness (`benchmark_scan.py`) times cold vs warm scans, emitting a markdown
  table of files/s — counts/timings only, never patient data. 40 tests; `ruff` clean.

## 0.5.0 — indexer performance & hygiene
- **Parallel header reads** in the indexer (`--workers`, default 8) with a `tqdm` progress
  bar — the scan is I/O-bound over network shares.
- **Opt-in persistent index cache** (`--cache PATH`): raw header records are reused across
  runs keyed by (path, mtime, size); patient keying is re-applied each run so changing
  `--group-by` stays correct. Turns a multi-hour re-scan into seconds on immutable archives.
- **Content-detection hardening**: `.dcm`/`.ima` skip the extra preamble open (halved I/O)
  yet a garbage `.dcm` is still rejected (SOPClassUID required when there is no real
  preamble); known non-DICOM extensions and >50 MB preamble-less files are never force-parsed.
- `ruff` clean; CI-ready. 35 tests.

## 0.4.1 — correct RT scope & per-patient verdict (council-driven)
- **`NOT_RT` status** for studies with no RT object → the ~629 follow-up-MR studies that a
  full-cohort scan wrongly flagged "incomplete RT chain" are now out of scope.
- **`build_rt_rollup`**: one verdict per patient, reconciling the chain across studies via
  *resolved* referenced UIDs (not naive object union → no false `OK`; not a generic graph →
  the anti-`FOR_INCONSISTENT` study-level fix is preserved). Statuses `NO_RT`/`OK`/`WARN`/
  `INCOMPLETE` + `fragmented` flag + named reason. A fragmented chain (RT objects spread
  across studies) gets one honest verdict instead of confusing per-study noise.
- CLI `rt-check` prints the per-patient rollup + a scoped KPI; `--detail` for per-study.

## 0.4.0 — adaptive indexer (council-driven)
- **Single indexer → canonical table**; analyses consume only the table, never the
  filesystem. The package adapts to arbitrary server layouts because identity comes from
  DICOM tags, not folder positions.
- **Content-based DICOM detection** (DICM preamble / parseable SOPClassUID), not the `.dcm`
  extension — recovers extensionless DICOM that the old scan missed.
- RT integrity assessed **per (patient, study)** (a re-plan is a distinct study, not a false
  `FOR_INCONSISTENT`). Timepoints derived from `StudyDate` with an explicit `UNMAPPED`
  state (never shown as `MISSING`). `--group-by {dicom,folder}`, `--dry-run` preflight that
  refuses to emit a report on an empty/ununderstood scan, run manifest.

## 0.3.0 — CLI
- Console script `dicom-discovery` (`demo` / `index` / `rt-check` / `completeness`).

## 0.2.0 — longitudinal completeness
- Observed-vs-expected completeness heatmap (self-contained Plotly, no CDN) against a YAML
  `Protocol`.

## 0.1.0 — RT chain integrity v2
- Header-only RT chain QC (links resolved against all present SOPInstanceUIDs; severity +
  confidence). Synthetic DICOM-RT cohort with ground truth; pytest suite. No PHI in repo.
