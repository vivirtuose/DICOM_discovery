# DICOM_discovery — Project Plan & Status

**Last updated:** 2026-09-23
**Branch:** `master` · **Version:** 0.11.0 · **Published:** [PyPI](https://pypi.org/project/DICOM-discovery/) + [GitHub release v0.11.0](https://github.com/vivirtuose/DICOM_discovery/releases/tag/v0.11.0)
**Health:** 212 tests pass, `ruff` clean. CI green on Python 3.9–3.14 (Ubuntu) plus Windows
and macOS legs; NAS bundle, standalone binaries and the release pipeline all green.

> Handoff document. Objectives 1–4 are complete and shipped. Overwrite this file when the
> next objective starts.

---

## Status by objective

| # | Objective | Status |
|---|---|---|
| 1 | Multi-Python (3.9–3.14) | ✅ proven by the CI matrix |
| 2 | One-command install | ✅ `pip install dicom-discovery` (PyPI, v0.11.0) |
| 3 | Synthetic / open-source data proof-of-work | ✅ T1 synthetic + `proof` job · T2a pydicom real RT files (offline) · T2b real TCIA linked chain (cached, skips offline) · T3 opt-in `real-cohort.yml` · `no-dicom-in-git` gate |
| 4 | Runs on a hospital NAS | ✅ `job` command, NAS-aware indexer, hardened Docker image, offline wheelhouses, `docs/NAS_DEPLOYMENT.md` |
| 5 | Runs on any computer | ✅ Windows/macOS CI legs, `doctor`, Excel-safe CSVs, standalone binaries + double-click launcher |

## What shipped, by version

- **0.9.0 — hospital NAS.** `dicom-discovery job` (timestamped run folders, `latest/` mirror,
  PHI-free `last_run.json`, overlap lock with heartbeat, retention, index cache on by default;
  exit 0/1/2/3/75). Indexer hardened for real shares: NAS recycle/snapshot/thumbnail folders
  pruned (a deleted patient no longer reappears via `#recycle`), unlistable directories
  reported as `PARTIAL` instead of silently skipped, bounded in-flight reads (queued futures
  alone were ~2 GB per 1M files), interned UIDs (~1.3 → ~0.9 GB per 1M records), geometric
  cache checkpoints (was quadratic I/O), atomic writes everywhere. Hardened `Dockerfile`,
  `deploy/nas/` compose + offline installers + systemd units, `nas-bundle.yml`.
- **0.9.1 — portability proven.** CI gained Python 3.14 and Windows/macOS legs (the README
  promised three OSes while only Ubuntu was tested). `doctor` command (environment +
  share/output probes, folder-picker availability, Windows long-path status; exit 1 on
  failure), `--version`, CSVs with a UTF-8 BOM so accented French actions open in Excel,
  stale `requirements.txt` removed.
- **0.10.0 — release pipeline.** `release.yml` (tag `v*` → version check → build + clean-venv
  smoke test → NAS bundle → **draft** release with `SHA256SUMS.txt`) and `publish-pypi.yml`
  (Trusted Publishing, OIDC, fires only on a published release or manual dispatch). PEP 639
  licence metadata + shipped `LICENSE`, classifiers, project URLs, absolute README links.
  Offline wheelhouses for Windows and macOS beside the six Linux ones.
- **0.11.0 — no Python required.** PyInstaller binaries (linux-x86_64 / windows-amd64 /
  macos-arm64), each *run* in CI as a user would, with an assertion that the produced report
  references no external script or stylesheet. Double-click launcher (`gui.py`): folder
  pickers → `job` → the report opens → the window waits. `docs/STANDALONE.md`.

## Two release-pipeline traps (found the hard way, now guarded)

1. An explicit `permissions:` block **drops every scope it does not name** — collecting the
   release assets needs `actions: read`.
2. Downloading *all* artifacts of a run also pulls buildx's `*.dockerbuild` metadata, whose
   download fails after 5 retries and takes the whole step down. Use an explicit `pattern:`.

Both are covered by tests in `tests/test_packaging.py`.

## Left to do (next session)

1. **First real deployment**: load the image on the NAS (or install a wheelhouse on the server
   mounting `/mnt/NAS2418_RADT`), run `job --dry-run`, confirm `--group-by folder`
   (PatientID = MRN on RADIO-AIDE), then schedule it.
2. **Measure on the real ~1M-file cohort**: peak RAM (the ~2 GB figure is an *estimate* from
   simulated records, never measured on real data) and cold/warm durations; then replace the
   estimate in `docs/NAS_DEPLOYMENT.md`.
3. **Re-run `rt-check` on the real cohort** to quote a per-patient verdict split in the
   package README (the historical figures there are per RT study; the per-patient rollup of
   the June 2026 run was never recorded).
4. *Optional:* code-signing for the standalone binaries (SmartScreen/Gatekeeper warn today);
   GHCR publication if the hospital network can reach it (deliberately not done).
5. *Optional:* Windows long-path handling in the indexer (`doctor` detects it, the walk does
   not work around it yet).

**Deferred:** pydicom-4.0 API migration (blocked by the 3.9 leg), GitHub Pages.

---

## How to run / test

```bash
cd DICOM_discovery              # the package lives in this subdirectory of the repo
py -m venv .venv                # local dev box: Python 3.14; package supports 3.9–3.14
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest        # 212 tests
./.venv/Scripts/ruff.exe check src synth tests bench

# See the tool work on synthetic data (no PHI), then open the report:
./.venv/Scripts/dicom-discovery.exe demo --out-dir examples
./.venv/Scripts/dicom-discovery.exe report --root examples --out examples/cohort_report.html
```

## Repo layout (gotchas)

- **Git repo root** = the outer `DICOM_discovery/` (holds `.git`; remote `origin` =
  `github.com/vivirtuose/DICOM_discovery`, branch `master`).
- **Python package** = the inner `DICOM_discovery/DICOM_discovery/` (`pyproject.toml`, `src/…`,
  `Dockerfile`, `deploy/`, `docs/`).
- **CI must live at the repo root** `.github/workflows/`: `ci.yml`, `nas-bundle.yml`,
  `binaries.yml`, `release.yml`, `publish-pypi.yml`, `real-cohort.yml`.
- No Docker on the Windows dev box: images are only built/tested in CI.
- No DICOM is committed (a CI job enforces it); every dataset is generated or fetched.
