"""Thin CLI wrapper — the generators now live in ``DICOM_discovery.synthetic``.

Kept for convenience: ``python synth/generate_longitudinal_cohort.py --out-dir DIR``.
Prefer ``dicom-discovery demo`` once the package is installed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from DICOM_discovery.synthetic import generate_longitudinal_cohort  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generate a synthetic longitudinal cohort.")
    p.add_argument("--out-dir", default="examples/longitudinal_cohort")
    args = p.parse_args(argv)
    notes = generate_longitudinal_cohort(args.out_dir)
    print(f"Wrote {len(notes)} synthetic patients to {args.out_dir}")
    for pid, desc in notes:
        print(f"  {pid}: {desc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
