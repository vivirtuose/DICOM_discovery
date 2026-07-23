# DICOM_discovery — Open-source DICOM data tiers (Objective 3) — Design

**Date:** 2026-07-23
**Status:** Approved decisions; awaiting maintainer spec review before implementation
**Branch:** `multi-env-packaging` (continues; one PR to `master` when Objective 3 lands)
**Depends on:** v0.8 packaging work (Objectives 1 & 2, already committed)

## Goal

Prove `DICOM_discovery` works on **real, openly-licensed** DICOM-RT data — not only its own
synthetic cohorts — as reproducible CI proof-of-work, with **no PHI** and **no per-push flakiness**.
Realism rises as frequency falls; anything network-bound is **non-blocking** (skips, never fails
the matrix).

## Confirmed decisions (maintainer, 2026-07-23)

- Build **all four tiers** this round.
- Fetch with the **standard library** (`urllib`) — no new dependency.
- One PR to `master` when Objective 3 is done.
- **Collection:** ~~Pelvic-Reference-Data~~ → **Vestibular-Schwannoma-SEG** (corrected: Pelvic-
  Reference-Data is CT-only. A live API probe showed RTPLAN exists in **exactly one** public
  collection — Vestibular-Schwannoma-SEG — which also has RTDOSE, RTSTRUCT and MR; 242/242
  patients carry a complete chain. It is **brain radiosurgery** data, directly aligned with the
  tool's brain-RT focus.)

## Council review — ratified modifications (2026-07-23)

Reviewed via the **llm-council** 3-stage protocol (Karpathy) — members Opus 4.8 / Sonnet / Haiku,
independent first opinions → anonymized peer ranking → chair synthesis. Aggregate ranking put
Sonnet 1st, Opus 2nd, Haiku 3rd; unanimous **APPROVE-WITH-CHANGES**. Final chair ruling:
**PROCEED-WITH-MODIFICATIONS**. The following are now binding on implementation:

- **[Grading — maintainer-ratified code change]** The rollup hardcodes "no CT → WARN +
  `récupérer le CT de planification`" (`rt_integrity.py:354-356`), and `build_rt_rollup` takes **no
  protocol argument** (only completeness is protocol-configurable) — so a `--protocol` YAML canNOT
  fix it. Make `build_rt_rollup` **MR-planning-aware**: when the planning image resolved through the
  chain is an MR (not CT) and the STRUCT→PLAN→DOSE chain resolves, do **not** cap to WARN with a
  "retrieve the CT" action. This makes the real-data showcase a correct verdict and improves the
  tool for genuine MR-planned radiosurgery. Covered by new tests; CT-based expectations preserved.
- **[T2b assertions]** Assert on the **actual** rollup columns (`rt_status`, `has_RTSTRUCT/RTPLAN/
  RTDOSE`, `reason`) and link behavior — **never** `rt_status == OK` as a blanket, and **not**
  GTV/CTV/PTV presence (VS ROIs are "Tumour"/"TV", so the target substring match legitimately
  reads absent). `plan_links_struct`/`dose_links_plan` do **not** exist in the `src/` rollup (only
  in the legacy `file_discovery/` copy) — do not assert on them.
- **[Fetch hardening]** stdlib `urllib` with an explicit **socket timeout and total timeout**, a
  small retry/backoff, and a broadened catch (`URLError`/`HTTPError`/timeout) so a *degraded-but-
  reachable* endpoint **skips, never hangs**.
- **[Cache integrity]** A SHA-256 mismatch on cached/restored files must **`pytest.skip` loudly, not
  fail** (TCIA re-de-identification changes bytes while SeriesInstanceUIDs stay stable). Verify
  hashes against **cache-restored** files, not only fresh downloads; fold the `provenance.json` hash
  into the `actions/cache` key.
- **[Governance]** Pin the **exact** citations now: Shapey et al. 2021 (dataset) **and** Clark et
  al. 2013 (TCIA infrastructure), per TCIA's Data Usage Policy. Add a CI gate that **fails if any
  `*.dcm` is tracked** (`git ls-files | grep -i '\.dcm$'`) — `.gitignore` is a convention, not a
  barrier.
- **[Workflow isolation]** T3 lives in its **own workflow file** with its own
  `on: workflow_dispatch + schedule` — `ci.yml`'s top-level `on: push/pull_request` governs the
  whole file, so gating only a job still registers a run on every push.
- **[Overruled]** A per-push offline "T2c" synthetic linked-chain test — **redundant**: T1 already
  exercises STRUCT→PLAN→DOSE resolution offline every push with ground truth.
- **[Sequencing]** Land T1 + T2a + the MR-aware grading change first (all offline); then T2b/T3.

## The four tiers

| Tier | Data | Runs | Proves |
|---|---|---|---|
| **T1 Synthetic** *(exists)* | `generate_*_cohort()`, labeled ground truth | Every push + `proof` job | Correctness vs known verdicts; end-to-end pipeline |
| **T2a pydicom real files** | `rtplan/rtstruct/rtdose.dcm` bundled with pydicom | Every push, **offline** | Indexer/parser survives **real vendor headers** |
| **T2b real linked chain** | 1 Vestibular-Schwannoma-SEG patient's RT objects, fetched + cached | Every push **if reachable**, else `skip` | Real **STRUCT→PLAN→DOSE resolution** on genuine cross-referenced UIDs |
| **T3 full public cohort** | N complete patients (MR + RT) from the same collection | **Opt-in**: `workflow_dispatch` + weekly `schedule` | Scale + realism; uploads a real-cohort report artifact |

### T2a — pydicom-bundled real files (offline, every push)
`pydicom.data.get_testdata_file("rtplan.dcm" | "rtstruct.dcm" | "rtdose.dcm")` returns three
genuine, vendor-authored RT objects. They are from **different sources** (distinct
StudyInstanceUIDs — not a linked chain), which is exactly what makes them a good robustness probe:
the indexer must detect all three by content, assign the right modality/SOPClass, and
`build_rt_integrity` must produce a coherent verdict on **unlinked real objects without crashing**.

### T2b — one real linked chain (fetched, cached, skips offline)
Fetch **only the three RT-object series** (RTSTRUCT, RTPLAN, RTDOSE — ~6 small files) of one pinned
patient (default `VS-SEG-001`). The STRUCT→PLAN→DOSE links are SOPInstanceUID references contained
entirely within those three objects, so the chain resolves without the (large) MR series. Absent
imaging simply yields "no planning image" — a realistic, expected signal, not a failure. The test
asserts the resolved chain + expected verdict; it **`pytest.skip`s** when the cache is empty and the
network is unavailable.

### T3 — full public cohort (opt-in)
Fetch several **complete** patients (MR + all RT objects) via `bench/fetch_public_cohort.py`, run the
real `report --json` pipeline, upload the HTML + verdict JSON as CI artifacts. Triggered by
`workflow_dispatch` (manual) and a weekly `schedule` — **never on push/PR**.

## Data source facts (verified via live API probe)

- Collection **Vestibular-Schwannoma-SEG**, TCIA. Modalities: `MR, RTSTRUCT, RTPLAN, RTDOSE`.
  242 patients; all complete. Example `VS-SEG-001`: MR×2, RTSTRUCT×2, RTPLAN×2, RTDOSE×2 (~206
  instances). Publication: Shapey et al., *Scientific Data* (2021). **License: CC BY 4.0**
  (attribution required — carried in the fetch script banner and the docs).
- API (public, **no auth**): `https://services.cancerimagingarchive.net/nbia-api/services/v1`
  - `/getSeries?Collection=…[&PatientID=…]` → series list (JSON; incl. SeriesInstanceUID, Modality).
  - `/getImage?SeriesInstanceUID=…` → a ZIP of that series' DICOM files.

## Components

- **`bench/fetch_public_cohort.py`** — stdlib-only (`urllib`, `json`, `zipfile`) CLI:
  `--collection`, `--patients N | --patient-id ID`, `--modalities`, `--out-dir`, `--rt-only`.
  Lists series → downloads each series ZIP → extracts to `out-dir/<PatientID>/<Modality>/`. Prints
  **counts + the CC BY 4.0 attribution only** (no patient data echoed). Content is **pinned**: it
  writes a `provenance.json` (collection, patient IDs, SeriesInstanceUIDs, per-file SHA-256) so a
  "real data" run cannot silently change meaning; a `--verify` mode re-checks hashes.
- **`tests/fixtures/real_data.py`** — `pydicom_rt_dir` (copies the 3 bundled files into a temp dir;
  always available) and `real_rt_chain_dir` (returns the cached pinned chain under
  `tests/.cache/vs_seg_chain/`, or fetches it once, or `pytest.skip` if unreachable and uncached).
- **`tests/test_real_data.py`** — T2a and T2b assertions (below).
- **CI** (`.github/workflows/ci.yml`): T2a runs inside the existing matrix (offline). T2b uses
  `actions/cache` keyed on the pinned SeriesInstanceUIDs; on a cache miss it fetches once, and the
  test skips (not fails) if the fetch is unavailable. New **`real-cohort`** job (`workflow_dispatch`
  + `schedule: weekly`) runs `fetch_public_cohort.py` → `report --json` → `upload-artifact`.
- **Docs** — a "Data tiers & provenance" section in the package README: the four tiers, the exact
  source, the **CC BY 4.0 attribution + citation**, and how to run each tier locally.

## Tests (TDD)

- **T2a:** indexing `pydicom_rt_dir` finds 3 DICOM instances; modalities == {RTPLAN, RTSTRUCT,
  RTDOSE}; SOPClassUIDs correct; `build_rt_integrity` / `build_rt_rollup` run and return a verdict
  for the (unlinked) objects **without raising**.
- **T2b (skippable):** indexing `real_rt_chain_dir` resolves RTPLAN→RTSTRUCT and RTDOSE→RTPLAN by
  referenced SOPInstanceUID; the rollup yields the expected verdict for a plan whose planning image
  is absent. Skips cleanly when offline + uncached.
- All existing tests stay green; `ruff` clean; runtime deps unchanged (fetch is stdlib + dev-only).

## Policy: PHI, licensing, determinism

- **No PHI, ever.** TCIA public collections are de-identified and openly licensed; nonetheless
  `.gitignore` blocks all `*.dcm` so **nothing real is committed** — data is fetched to gitignored
  cache/output dirs only.
- **Attribution.** CC BY 4.0 requires credit: the fetch script and docs carry the citation.
- **Determinism.** Pinned SeriesInstanceUIDs + per-file SHA-256 in `provenance.json`; `--verify`.

## Done when

- T2a runs green in the matrix (offline). T2b passes when data is present and **skips** (never
  fails) when not. `fetch_public_cohort.py` fetches `VS-SEG-001`'s chain and a small T3 cohort
  locally; `report --json` produces a self-contained HTML + schema-valid JSON on real data.
- `real-cohort` CI job defined (dispatch + schedule). README provenance/attribution section added.
- Full suite green, `ruff` clean, runtime deps unchanged. `DICOM_plan.md` overwritten with the
  finished state; single PR to `master`.

## Out of scope (this round)

- Committing any real DICOM into git. Multiple collections. A second (PACS/QIDO) fetch backend.
- pydicom-4.0 migration, GitHub Pages, PyPI, Windows/macOS CI (tracked elsewhere).
