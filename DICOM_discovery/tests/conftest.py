"""Shared fixtures: build the synthetic cohorts once and index/assess them."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import (  # noqa: E402
    build_index,
    build_rt_integrity_from_dir,
    generate_longitudinal_cohort,
    generate_synthetic_cohort,
)


@pytest.fixture(scope="session")
def cohort(tmp_path_factory):
    """(rt_df, IndexResult, {patient_id: GroundTruth}) for the synthetic RT cohort."""
    out = tmp_path_factory.mktemp("synthetic_cohort")
    truth = generate_synthetic_cohort(str(out))
    df, idx = build_rt_integrity_from_dir(str(out))
    return df, idx, {t.patient_id: t for t in truth}


@pytest.fixture(scope="session")
def longitudinal(tmp_path_factory):
    """(root_path, IndexResult) for the synthetic longitudinal cohort."""
    out = tmp_path_factory.mktemp("longitudinal_cohort")
    generate_longitudinal_cohort(str(out))
    return str(out), build_index(str(out))


# Tier 2a — REAL DICOM-RT data (offline). pydicom ships three genuine, vendor-authored RT
# objects; using them proves the indexer/parser survives real-world headers, not only our
# synthetic cohort. They come from different sources (unlinked), which is exactly what makes
# them a good robustness probe.
_PYDICOM_RT_FILES = ("rtplan.dcm", "rtstruct.dcm", "rtdose.dcm")


@pytest.fixture(scope="session")
def pydicom_rt_dir(tmp_path_factory):
    """A directory containing pydicom's three bundled real RT objects (offline)."""
    from pydicom.data import get_testdata_file

    out = tmp_path_factory.mktemp("pydicom_rt")
    for name in _PYDICOM_RT_FILES:
        try:
            src = get_testdata_file(name)
        except Exception:
            src = None
        if not src or not Path(src).exists():
            pytest.skip(f"pydicom bundled RT file {name} unavailable")
        shutil.copy(src, out / name)
    return out
