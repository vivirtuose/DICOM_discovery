# bench/ — traversal benchmark (dev tooling)

`benchmark_scan.py` times `build_index` on a tree: a **cold** scan (empty cache, every file
read) followed by a **warm** scan that reuses an `--assume-immutable` cache (no per-file
stat). It emits a markdown table of wall-time and files/s, and a warm-vs-cold speedup.

This is **not** part of the package import path — it is throwaway dev tooling and is not
shipped via `pyproject`. It prints **only counts and timings**: no patient ids, no patient
file paths, no DICOM values.

## Run on the synthetic cohort (no PHI)

```bash
python -m DICOM_discovery demo --out-dir /tmp/dd_demo
python bench/benchmark_scan.py --root /tmp/dd_demo/synthetic_cohort
```

## Run on a bounded NAS subset (a few `IC` patients)

Point it at a *subset* directory (2–3 patients, a few thousand files) for a fast loop:

```bash
python bench/benchmark_scan.py --root /mnt/NAS2418_RADT/.../DICOM/<subset> --workers 8
```

## Headline numbers

The **real before/after** on the full ~1M-file cohort
(`/mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM`) is produced by the
orchestrator, manually or in the background (~15–30 min per run). Do not run the full cohort
unattended or in CI — and never let benchmark output include patient data.
