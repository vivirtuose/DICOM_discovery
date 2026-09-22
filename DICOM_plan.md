# DICOM_discovery — Project Plan & Status

**Last updated:** 2026-09-22
**Branch:** `master` · **Version:** 0.9.0
**Health:** 183 tests pass, `ruff` clean; CI green on Python 3.9–3.13; NAS bundle built and
smoke-tested in CI (see below).

> Handoff document. Objectives 1–3 (v0.8) are complete; v0.9 adds the hospital-NAS
> deployment. Overwrite this file when the next objective starts.

---

## Status by objective

| # | Objective | Status |
|---|---|---|
| 1 | Multi-Python (3.9–3.13) | ✅ proven by the CI matrix |
| 2 | One-command install from GitHub | ✅ clean-room proven |
| 3 | Synthetic / open-source data proof-of-work | ✅ T1 synthetic + `proof` job · T2a pydicom real RT files (offline) · T2b real TCIA linked chain (cached, skips offline) · T3 opt-in `real-cohort.yml` (weekly + manual) · `no-dicom-in-git` gate |
| 4 | **Runs on a hospital NAS** (v0.9.0) | ✅ `dicom-discovery job` + NAS-aware indexer + hardened Docker image + offline wheelhouse + `docs/NAS_DEPLOYMENT.md` |

## v0.9.0 — hospital NAS (what exists)

- **`dicom-discovery job --root … --output-dir …`** — unattended scheduled run: `runs/<UTC>/`,
  `latest/` (atomic mirror of the last usable run), PHI-free `last_run.json`, overlap lock with
  heartbeat + stale takeover, retention `--keep`, index cache on by default, never writes into
  the scanned tree. Exit codes 0 ok / 1 no DICOM / 2 error / 3 partial / 75 locked.
- **Indexer hardening** — prunes NAS recycle/snapshot/thumbnail folders (a deleted patient no
  longer reappears via `#recycle`), reports unlistable directories (`PARTIAL`), skips client
  litter, bounded in-flight reads (was ~2 GB of queued futures per 1M files), interned UIDs,
  geometric cache checkpoints (was quadratic), atomic writes everywhere.
- **Deployment** — `DICOM_discovery/Dockerfile` (non-root, default cmd = `job`),
  `deploy/nas/docker-compose.yml` (network none, DICOM `:ro`, read-only rootfs, cap_drop ALL,
  RAM/CPU caps) + `.env.example`, `install-offline.sh`, systemd service/timer.
- **CI `nas-bundle.yml`** — builds the image for amd64 + arm64, smoke-tests it under NAS
  constraints, uploads `docker load` tarballs; builds + proves offline wheelhouses py3.9–3.13.
  Artifacts: GitHub → Actions → *NAS bundle* → latest run on `master`.

## Left to do (next session)

1. **First real deployment**: load the image on the NAS (or install the wheelhouse on the
   server mounting `/mnt/NAS2418_RADT`), run `job --dry-run`, confirm `--group-by folder`
   (PatientID = MRN on RADIO-AIDE), then schedule it.
2. **Measure on the real ~1M-file cohort**: peak RAM (estimate ~2 GB, default cap 3 GB) and
   cold/warm durations; update `docs/NAS_DEPLOYMENT.md` sizing with real numbers.
3. *Optional:* publish the image to a registry (GHCR) if the hospital network can reach it
   — deliberately not done: artifacts only, nothing published.
4. *Optional:* `git add --renormalize .` for the LF policy (deferred churn).

**Deferred:** pydicom-4.0 API migration (blocked by the 3.9 leg), GitHub Pages, PyPI,
Windows/macOS CI legs.

---

## How to run / test

```bash
cd DICOM_discovery              # the package lives in this subdirectory of the repo
py -m venv .venv                # local dev box: Python 3.14; package supports 3.9–3.13
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest        # 183 tests
./.venv/Scripts/ruff.exe check src synth tests bench
```

## Repo layout (gotchas)

- **Git repo root** = the outer `DICOM_discovery/` (holds `.git`; remote `origin` =
  `github.com/vivirtuose/DICOM_discovery`, branch `master`).
- **Python package** = the inner `DICOM_discovery/DICOM_discovery/` (`pyproject.toml`, `src/…`,
  `Dockerfile`, `deploy/nas/`, `docs/`).
- **CI must live at the repo root** `.github/workflows/` (`ci.yml`, `nas-bundle.yml`,
  `real-cohort.yml`); workflows use `working-directory: DICOM_discovery`.
- No Docker on the Windows dev box: the image is only built/tested in CI.
