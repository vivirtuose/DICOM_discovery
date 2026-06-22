"""Dev-only benchmark for ``build_index`` traversal speed.

NOT part of the package import path — this is throwaway tooling. It times a **cold** scan
(empty cache) then a **warm** scan reusing a ``--assume-immutable`` cache, reporting files/s
and wall-time as a markdown table.

PRIVACY: it prints ONLY counts and timings. It never prints patient ids, paths of patient
data, DICOM values, or anything derived from file contents. Safe to paste into a report.

Usage
-----
    python bench/benchmark_scan.py --root /path/to/tree [--workers 8]

The synthetic smoke run (no PHI):
    python -m DICOM_discovery demo --out-dir /tmp/dd_demo
    python bench/benchmark_scan.py --root /tmp/dd_demo/synthetic_cohort

Do NOT point this at the NAS in CI or unattended — the real headline numbers (cold/warm on
the ~1M-file cohort) are produced by the orchestrator, manually/in background.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

# Make the package importable when run from the repo without installation.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import build_index  # noqa: E402


def _time_scan(root: str, workers: int, cache: str | None, assume_immutable: bool):
    t0 = time.perf_counter()
    idx = build_index(root, progress=False, workers=workers, cache=cache,
                      assume_immutable=assume_immutable)
    elapsed = time.perf_counter() - t0
    m = idx.manifest
    return elapsed, int(m["n_files_seen"]), int(m["n_dicom_indexed"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Benchmark build_index cold vs warm (counts/timings only).")
    p.add_argument("--root", required=True, help="directory tree to scan")
    p.add_argument("--workers", type=int, default=8, help="parallel header-read threads (default 8)")
    args = p.parse_args(argv)

    with tempfile.TemporaryDirectory() as td:
        cache = str(Path(td) / "bench.cache")

        # Cold: no cache yet (the file does not exist), so every file is read.
        cold_s, n_files, n_dicom = _time_scan(args.root, args.workers, cache, assume_immutable=False)
        # Warm: reuse the cache from the cold run, immutable fast path (no per-file stat).
        warm_s, _nf, _nd = _time_scan(args.root, args.workers, cache, assume_immutable=True)

    def fps(elapsed: float) -> float:
        return n_files / elapsed if elapsed > 0 else float("nan")

    speedup = (cold_s / warm_s) if warm_s > 0 else float("nan")

    print("\n## build_index benchmark (counts/timings only — no patient data)\n")
    print(f"- files seen: **{n_files}**")
    print(f"- DICOM indexed: **{n_dicom}**")
    print(f"- workers: **{args.workers}**\n")
    print("| run | mode | wall-time (s) | files/s |")
    print("| --- | --- | ---: | ---: |")
    print(f"| cold | full read | {cold_s:.3f} | {fps(cold_s):,.0f} |")
    print(f"| warm | --assume-immutable cache | {warm_s:.3f} | {fps(warm_s):,.0f} |")
    print(f"\n**warm speedup: {speedup:.1f}x** (cold / warm wall-time)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
