# DICOM_discovery — Project Plan & Status

**Last updated:** 2026-07-23
**Branch:** `multi-env-packaging` (off `master`) · **Version:** 0.8.0
**Local health:** 140 tests pass, `ruff` clean, `pip check` clean (Python 3.14 locally; CI matrix targets 3.9–3.13).

> Handoff document. Overwrite this file when Objective 3 is complete.

---

## Objectives (given by maintainer, 2026-07-23)

1. **Multi-Python-env compatible** package.
2. **One-command install** (from GitHub).
3. **Synthetic / open-source data architecture as CI proof-of-work** — prove the package works
   on realistic data, no PHI.

All work is **RUO** (research use only) and preserves the non-negotiables: indexer → canonical
table → pure consumers; header-only reads; self-contained air-gapped HTML; no PHI in git.

---

## Status by objective

### ✅ Objective 1 — Multi-Python (3.9–3.13) — DONE (committed, not yet proven in CI)
- `requires-python >=3.9` (dropped EOL 3.8); ruff target `py39`.
- Deprecated `datetime.utcnow()` → timezone-aware UTC (identical `…SSZ` timestamp).
- Synthetic generators kept on the pydicom write API valid on **both** pydicom 2.4 (only line
  supporting 3.9) and 3.x; the three pydicom-4.0-removal `DeprecationWarning`s are silenced.
- CI workflow moved to the **repo root** (`.github/workflows/ci.yml`) with a **3.9–3.13 matrix**.
- *Becomes "real" once the branch is pushed and Actions runs the matrix.*

### ✅ Objective 2 — One-command install — DONE + clean-room proven
- **plotly** promoted from the optional `[viz]` extra to a **core dependency** → the headline
  `report` works from a bare install.
- **PyYAML** declared as a core dep (was imported-but-undeclared → clean install failed).
- **Windows cp1252 CLI crash fixed** (`_force_utf8_stdio` in `main()`).
- One-command install documented in both READMEs + status badges.
- **Proven** in a fresh venv, non-editable install (the exact `git+…#subdirectory` build path):
  `dicom-discovery demo` + `report --json` run end-to-end → self-contained 4.9 MB HTML +
  schema-valid `verdicts.json` (`tool_version 0.8.0`).
- Install command:
  ```bash
  pip install "git+https://github.com/vivirtuose/DICOM_discovery.git#subdirectory=DICOM_discovery"
  ```

### 🚧 Objective 3 — Open-source data proof-of-work — IN PROGRESS
- **Done + committed:** a CI `proof` job that synthesises an open longitudinal cohort and runs
  the real `report --json` pipeline, uploading the HTML + verdict JSON as artifacts (no PHI).
- **Designed, not yet implemented:** a **four-tier open-source DICOM data architecture** (below),
  chosen with the maintainer ("both tiers; both real-data sources").

---

## Objective 3 — agreed design (four tiers of increasing realism)

Principle: **realism goes up as frequency goes down**; anything touching the network is
**non-blocking** (skips, never breaks the matrix).

| Tier | Data | When it runs | Proves |
|---|---|---|---|
| **T1 Synthetic** *(exists)* | `generate_*_cohort()` — labeled ground truth | Every push + `proof` job | Correctness vs known verdicts; end-to-end pipeline |
| **T2a pydicom real files** | `rtplan/rtstruct/rtdose.dcm` (ship with pydicom) | Every push, **offline** | Indexer/parser survives **real vendor headers** |
| **T2b real linked chain** | 1 public patient (CT→RTSTRUCT→RTPLAN→RTDOSE), fetched + cached | Every push **if reachable**, else **skips** | Real **chain resolution** on genuine cross-referenced UIDs |
| **T3 full public cohort** | Several patients from a TCIA RT collection | **Opt-in**: `workflow_dispatch` + weekly `schedule` | Scale + realism; uploads a real-cohort report artifact |

**Grounding fact (verified):** pydicom (already a dependency) bundles three real, vendor-authored
RT objects — `rtplan.dcm`, `rtstruct.dcm`, `rtdose.dcm` — genuine headers but from different
sources (unlinked). Perfect zero-download source for T2a.

### Components to build
- `tests/fixtures/real_data.py` — `pydicom_rt_dir` fixture (copies the 3 bundled files) and
  `real_rt_chain_dir` fixture (returns cached fetched chain or `pytest.skip(...)`).
- `tests/test_real_data.py` — T2a (indexer detects all 3, correct modalities/SOPClasses,
  `build_rt_integrity` doesn't crash on unlinked real objects); T2b (real chain resolves to the
  expected verdict; skipped when data absent).
- `bench/fetch_public_cohort.py` — documented CLI pulling a bounded TCIA subset via the **TCIA
  REST API**, header/counts only. Serves both T2b (1 patient, cached) and T3 (N patients).
- **CI:** T2a/T2b ride the existing `test` matrix (`actions/cache` for the chain); a new
  `real-cohort` job (`workflow_dispatch` + `schedule`) runs fetch → `report --json` → upload.
- **Docs:** "Data tiers & provenance" section — sources, **licenses/attribution** (pydicom
  test-data license; TCIA collection citation + CC BY), how to run each tier locally.

### ❓ Open decisions (blocking implementation — ask maintainer)
1. **T3 collection:** recommend **TCIA "Pelvic-Reference-Data"** (CC BY 3.0, purpose-built RT
   reference set with complete chains; doubles as the T2b cached patient). Fallback:
   **NSCLC-Radiomics**.
2. **Fetch tool:** recommend **stdlib `urllib`** (no new dependency) vs. the community
   `tcia_utils` package (cleaner code, adds a dev/CI dep).

### Design safeguards
- No PHI ever; nothing real is committed (`.gitignore` blocks all `*.dcm`) — data is fetched/cached.
- The fetched chain is **pinned** (SeriesInstanceUID + content checksum) so a "real data" test
  can't silently change meaning.

---

## Commits on `multi-env-packaging` (newest first)

```
3fe0946 docs+chore: v0.8 changelog, design doc, LF normalization, ignore dev tooling
df76239 docs(install): one-command install + status badges (objective 2)
7ad0c12 ci: matrix over Python 3.9-3.13 + synthetic-data proof-of-work job
db478ee fix(cli): force UTF-8 stdio so report/preflight can't crash on cp1252
58c7c2b feat(compat): target Python 3.9-3.13, plotly core, fix utcnow deprecation
7ffc107 fix(deps): declare PyYAML as a core dependency
8c60fea Improve per-study drill-down in cohort report   (pre-existing base)
```

---

## Left to do (next session)

1. **Resolve the two open Objective-3 decisions** (T3 collection, fetch tool).
2. **Implement Objective 3**: T2a/T2b fixtures + tests, `fetch_public_cohort.py`, the
   `real-cohort` CI job, and the provenance docs. Then **overwrite this file** with the result.
3. **Watch CI** after push: confirm the 3.9–3.13 matrix is green and the `proof` job uploads
   artifacts (objectives 1 & 3 only become "real in GitHub" once CI runs).
4. **Open a PR** `multi-env-packaging → master` when the maintainer is ready.
5. *Optional:* one-time `git add --renormalize .` so existing files adopt the new `.gitattributes`
   LF policy (deferred — avoids a large line-ending-only churn commit).

**Deferred (out of scope this round):** pydicom-4.0 API migration (blocked by the 3.9 leg),
GitHub Pages hosting of the demo report, PyPI publication, Windows/macOS CI legs.

---

## How to run / test

The original `epibrainrad` conda env no longer exists. Work happens in a local venv:

```bash
cd DICOM_discovery              # the package lives in this subdirectory of the repo
py -m venv .venv                # Python 3.14 locally; package supports 3.9–3.13
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest        # 140 tests
./.venv/Scripts/ruff.exe check src synth tests
```

## Repo layout (gotchas)

- **Git repo root** = the outer `DICOM_discovery/` (holds `.git`; remote `origin` =
  `github.com/vivirtuose/DICOM_discovery`, branch `master`).
- **Python package** = the inner `DICOM_discovery/DICOM_discovery/` (`pyproject.toml`, `src/…`).
- **CI must live at the repo root** `.github/workflows/` (GitHub Actions ignores the nested
  package `.github/`); the workflow uses `defaults.run.working-directory: DICOM_discovery`.
