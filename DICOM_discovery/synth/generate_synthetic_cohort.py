"""Thin CLI wrapper — the generators now live in ``DICOM_discovery.synthetic``.

Kept for convenience: ``python synth/generate_synthetic_cohort.py --out-dir DIR``.
Prefer ``dicom-discovery demo`` once the package is installed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from DICOM_discovery.synthetic import generate_synthetic_cohort  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generate a synthetic DICOM-RT chain cohort.")
    p.add_argument("--out-dir", default="examples/synthetic_cohort")
    args = p.parse_args(argv)
    truth = generate_synthetic_cohort(args.out_dir)
    print(f"Wrote {len(truth)} synthetic patients to {args.out_dir}")
    for t in truth:
        print(f"  {t.patient_id}: expected {t.expected_status:<10} — {t.description}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
