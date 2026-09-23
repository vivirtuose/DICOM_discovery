# Changelog

All notable changes to **DICOM_discovery**. The package began as a single-institution RT
data-curation script and was rebuilt, in council-reviewed increments, into an adaptive
cohort-QC tool.

## 0.12.0 — Completeness redesigned: a cohort gap table, not a heatmap (2026-09-23)
- **Why the view exists at all.** On the synthetic longitudinal cohort, patient L004 is
  graded `OK` by the RT integrity tab — its treatment chain is complete and consistent —
  while it has lost all three follow-up MRIs (M3, M6, M12). The RT view structurally cannot
  see that; completeness is what makes a patient like L004 visible before a longitudinal
  study gets built on them.
- **The section now leads with a cohort gap table** (`Timepoint | Modality | Patients
  missing | Who`), one row per (timepoint, modality) pair that is missing for at least one
  patient, worst first — "chase the M6 MR for these two patients" is the action a QC round
  actually takes. The per-patient grid sits below it, collapsed by default inside a native
  `<details>` drill-down, everything it had before (filter, "only incomplete" toggle, sticky
  header, CSV export) intact. With no gaps at all, the table is replaced by a single line,
  `No gap: every expected object is present.`, instead of rendering empty.
- **The old heatmap was a liability, not just ugly.** 24 flat `timepoint | modality` column
  labels, tilted -45 degrees, produced 2,352 cells and a 4,120 px tall figure at 98 patients
  — of which 66% were grey "not expected" boxes carrying no information, and every one of
  them coded by colour alone.
  The grid now has one column per protocol timepoint (4, not 24), a cell with nothing
  expected draws no ink at all, and every state (present/missing/extra/unmapped) is carried
  four ways — CSS class, glyph, a screen-reader-only word, and an `aria-label` — so the page
  reads without colour.
- **A worst-gap KPI and a protocol line** now sit above both the gap table and the grid: a
  headline count of patients complete, the most-missed timepoint/modality pair and how many
  patients it affects, a "nothing expected" segment kept separate from "100% complete" (a
  patient with zero expected objects is not the same claim as one with 8/8 present, and the
  ruling keeps them visually distinct), and a note for timepoint cells that could not be
  placed on the protocol window at all. The protocol line states, once, what each timepoint
  actually expects (`M12  day 360 +/-60  MR`), so a MISSING chip is a claim a reader can
  check rather than an unexplained verdict.
- **The standalone completeness page drops its Plotly payload.** The old file embedded the
  whole charting bundle to draw coloured rectangles: 4.9 MB for a 5-patient demo. The new
  page has no chart library and no external reference at all (no `<script src=`, no
  `<link href=`, opens air-gapped by double-click) — 38 KB for the same 5 patients, ~274 KB
  at 98 patients.

## 0.11.1 — the Action column speaks to the clinician (2026-09-23)
- **Recommended actions are now plain English**, not French, and each names the object it is
  about: "Re-export RTDOSE from the planning system or PACS" instead of "récupérer le(s)
  objet(s) manquant(s) sur le PACS". A physician running the package on their own data reads
  one instruction and knows what to do.
- The missing-target action also says the names can simply be non-standard ("Add the target
  contour(s), or rename them to standard names: PTV") — the TG-263 check is substring-based,
  so a target contoured as "Tumour" reads as missing.
- Actions are ASCII, so they survive any Excel codepage in the CSV export; a test rejects
  French leftovers, over-long text and unnamed objects.

## 0.11.0 — runs on a machine with no Python: standalone binaries + double-click launcher (2026-09-22)
- **One self-contained executable per OS** (Windows `.exe`, macOS arm64, Linux x86_64), built
  by PyInstaller from `deploy/standalone/dicom-discovery.spec`. It embeds Python, pydicom,
  pandas and — critically — plotly's JS bundle, so the report it writes still opens on an
  air-gapped machine. ~50 MB, no installation, no admin rights.
- **Double-click launcher** (`gui.py`): a frozen binary started with no arguments no longer
  hits argparse's "arguments are required" in a window that closes instantly. It asks for the
  DICOM folder and the output folder (tkinter picker, typed-path fallback), runs the ordinary
  `job`, opens the report and holds the window open. Called *with* arguments — from a
  scheduler — it behaves exactly like the installed CLI. `--gui` forces it without freezing.
- **CI `binaries.yml`** builds all three, runs each produced binary (`--version`, `doctor`,
  `demo`, a full `job`) and asserts the report references no external script or stylesheet —
  the failure mode a frozen build hides. `release.yml` ships them as release assets.
- **`docs/STANDALONE.md`** (French): download, SmartScreen/Gatekeeper on unsigned binaries,
  the double-click flow, and the limits (size, start-up time, one file per architecture).

## 0.10.0 — installable from anywhere: PyPI-ready, signed-off releases, per-OS offline bundles (2026-09-22)
- **Release pipeline.** Pushing a tag `vX.Y.Z` runs `release.yml`: it refuses a tag that
  disagrees with `pyproject.toml`, builds the sdist + wheel, runs `twine check`, installs the
  wheel in a clean venv and exercises `--version`/`doctor`/`demo`/`job` end to end, rebuilds
  the NAS bundle, and gathers everything with a `SHA256SUMS.txt` into a **draft** GitHub
  release. Release assets do not expire, unlike the 90-day Actions artifacts.
- **PyPI publishing via Trusted Publishing** (`publish-pypi.yml`, OIDC — no API token in the
  repo). It never runs on a push or a tag: only when a human publishes the GitHub release, or
  dispatches it manually (with a TestPyPI rehearsal option). Setup: `docs/RELEASING.md`.
- **Distribution metadata**: PEP 639 `license = "MIT"` + a real `LICENSE` file shipped in the
  wheel, classifiers for Python 3.9–3.14 and the medical/research topics, project URLs, and a
  README whose links are absolute so the PyPI page does not 404.
- **Offline wheelhouses for Windows (`windows-amd64`) and macOS (`macos-arm64`)** beside the
  six Linux ones, each proven by installing with no package index and running a full `job`;
  new `deploy/nas/install-offline.ps1` is the Windows counterpart of the POSIX installer.

## 0.9.1 — portability: proven on Windows/macOS, environment doctor, Excel-safe CSVs (2026-09-22)
- **CI now proves the claim.** The matrix adds **Python 3.14** and two extra legs per OS on
  **Windows** and **macOS** (oldest + newest supported Python). Until now the README promised
  Linux/macOS/Windows while only Ubuntu was tested — the cp1252 console crash fixed in 0.8 was
  exactly that family of bug.
- **`dicom-discovery doctor`** — prints tool/Python/platform/dependency versions, stdout,
  filesystem and locale encodings, and (on Windows) whether long paths >260 chars are enabled;
  with `--root` / `--output-dir` it probes the share for readability and the output folder for
  writability. Exit 1 when a check fails, so it can gate a scheduled run.
- **`--version` / `-V`** on the CLI (it had none — awkward in a bug report).
- **CSVs are written with a UTF-8 BOM** so the French verdict actions ("récupérer l'image de
  planification") open correctly in Excel instead of as mojibake; `verdicts.json` stays
  BOM-free for strict JSON parsers.
- **`requirements.txt` removed.** It pinned a Python-3.8-era set (pandas 2.0.3, pydicom 2.4.4)
  that contradicted `pyproject.toml` and cannot span 3.9–3.14; `pyproject.toml` is the single
  source of truth, and the per-Python offline wheelhouses are the reproducible artifact.

## 0.9.0 — runs unattended on a hospital NAS (2026-09-22)
Makes the tool deployable on an air-gapped hospital NAS (or a server mounting one) and safe to
schedule. RUO throughout.
- **`dicom-discovery job`** — the scheduled run: one timestamped folder per run (report,
  verdict JSON, CSVs, index manifest, `run.log`); `latest/` mirrors the last usable run with
  atomic per-file replaces; PHI-free `last_run.json` for monitoring; overlap lock with
  stale-lock takeover; retention (`--keep`); index cache on by default; never writes into the
  scanned tree. Exit codes 0 ok · 1 no DICOM · 2 error · 3 partial scan · 75 locked.
- **NAS-aware indexer.** Recycle bins, snapshots and thumbnail stores (Synology `#recycle`/
  `#snapshot`/`@eaDir`, QNAP `@Recycle`/`@Recently-Snapshot`, NetApp `.snapshot`,
  `$RECYCLE.BIN`, macOS litter) are pruned — a deleted patient no longer reappears through
  the recycle bin; `--exclude-dir` adds site patterns. Directories that cannot be listed are
  reported (`n_dirs_unreadable`, `PARTIAL` in the preflight and the report header) instead
  of `os.walk`'s silent skip. AppleDouble `._*.dcm` files are no longer counted unreadable.
- **Bounded memory on ~1M-file shares.** Header reads stream through a bounded window
  (`Executor.map` queued every path up front: ~2 GB measured per million files); series-level
  UIDs are interned (~1.3 → ~0.9 GB per million records); series collapse before copying.
- **Linear cache I/O.** Cache checkpoints at doubling intervals instead of a full rewrite every
  5000 files (quadratic on ~1M files). Cache and all outputs are written atomically (temp +
  fsync + rename), so a killed run or a dropped share never leaves a truncated file.
- **Deployment.** Hardened `Dockerfile` (non-root, read-only-rootfs compatible, default
  command = `job`); `deploy/nas/` with a Synology/QNAP `docker-compose.yml` (no network,
  read-only DICOM mount, capped RAM/CPU), `.env.example`, an offline `install-offline.sh`
  and systemd timer units; French guide `docs/NAS_DEPLOYMENT.md`. New **NAS bundle** CI
  workflow builds the image (amd64 + arm64), smoke-tests it under NAS constraints and uploads
  `docker load`-able tarballs plus offline wheelhouses for Python 3.9–3.13. 183 tests.

## 0.8.0 — multi-Python packaging + synthetic-data CI proof-of-work (2026-07-23)
Makes the package portable, installable in one command, and self-proving in CI. RUO throughout.
- **Runs on Python 3.9–3.13.** `requires-python` raised to `>=3.9` (EOL 3.8 dropped); ruff
  target `py39`. Deprecated `datetime.utcnow()` replaced with timezone-aware UTC (identical
  `…SSZ` manifest/verdict timestamp). The synthetic generators keep the pydicom write API valid
  on **both** pydicom 2.4 (the only line supporting 3.9) and 3.x; the three pydicom-4.0-removal
  `DeprecationWarning`s are silenced as expected noise (full 4.0 migration deferred until 3.9 is
  dropped).
- **One-command install:** `pip install "git+https://github.com/vivirtuose/DICOM_discovery.git#subdirectory=DICOM_discovery"`.
  `plotly` is now a **core dependency** (was the optional `[viz]` extra), so the headline HTML
  report works out of the box. **PyYAML** is now correctly declared — `load_protocol()` imported
  it without declaring it, so a clean install failed on the protocol path.
- **Windows-safe CLI:** `main()` reconfigures stdout/stderr to UTF-8, so the preflight's
  box-drawing output no longer crashes an otherwise-successful `report` on a cp1252 console.
- **CI matrix + proof-of-work.** The workflow (moved from a nested, never-discovered path to the
  repo root) tests the whole 3.9–3.13 matrix on Ubuntu, then a `proof` job installs from clean,
  synthesises an **open** longitudinal cohort (no PHI), runs the real `report --json` pipeline,
  and uploads the self-contained HTML + verdict JSON as downloadable artifacts. 140 tests; `ruff`
  clean.
- **MR-based planning is valid.** The RT grading no longer demands a planning *CT*: a CT **or**
  an MR sharing the RT frame of reference counts as the planning image (brain radiosurgery is
  routinely MR-planned). The finding is renamed `MISSING_CT` → `MISSING_PLANNING_IMAGE` and the
  remediation reads "récupérer l'image de planification (CT/MR)".
- **Real-data test tiers (objective 3).** T2a: pydicom's three bundled, vendor-authored RT
  objects are indexed and graded offline on every push. T2b: one real linked
  RTSTRUCT→RTPLAN→RTDOSE chain (TCIA *Vestibular-Schwannoma-SEG*, CC BY 4.0) is fetched by
  `bench/fetch_public_cohort.py` (stdlib only), pinned by SHA-256, cached in CI, and **skips —
  never fails — when TCIA is unreachable**. T3: an opt-in `real-cohort.yml` workflow
  (`workflow_dispatch` + weekly) runs the real report on a small public cohort and uploads it.
  A `no-dicom-in-git` CI gate fails the build if any `*.dcm`/`*.ima` is ever tracked. 147 tests.

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
