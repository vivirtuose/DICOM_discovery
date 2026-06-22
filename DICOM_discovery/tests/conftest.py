"""Shared fixtures: build the synthetic cohorts once and index/assess them."""
from __future__ import annotations

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
