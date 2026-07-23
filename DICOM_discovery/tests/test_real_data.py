"""Tier 2a — the indexer/parser must survive REAL vendor-authored RT headers, not only the
synthetic cohort.

Uses the three RT objects bundled with pydicom (offline, an install dependency). They come
from different sources (unlinked — different patients/studies), so each is legitimately
INCOMPLETE. The point of this tier is that genuine real-world headers are detected by content,
parsed, and graded coherently **without crashing** — the failure this guards against is the
parser choking on a real vendor header the synthetic generator never produces.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery.indexer import build_index  # noqa: E402
from DICOM_discovery.rt_integrity import build_rt_integrity, build_rt_rollup  # noqa: E402


def test_indexer_detects_real_pydicom_rt_objects(pydicom_rt_dir):
    idx = build_index(str(pydicom_rt_dir))
    assert idx.manifest["n_dicom_indexed"] == 3
    assert set(idx.table["modality"]) == {"RTPLAN", "RTSTRUCT", "RTDOSE"}


def test_real_rt_objects_graded_without_crashing(pydicom_rt_dir):
    idx = build_index(str(pydicom_rt_dir))
    study = build_rt_integrity(idx.table)
    rollup = build_rt_rollup(idx.table)
    # Three unlinked single objects -> three distinct patients, each INCOMPLETE (missing the
    # other core modalities). Coherent, not a crash.
    assert len(rollup) == 3
    assert set(rollup["rt_status"]) == {"INCOMPLETE"}
    assert not study.empty
    assert set(study["rt_status"]) == {"INCOMPLETE"}
