"""MR-based radiotherapy planning (e.g. brain radiosurgery) must not be mis-scored.

The rollup historically hardcoded "no CT -> WARN + retrieve the planning CT". That is wrong
for MR-planned SRS, where the planning image is an MR and no CT ever existed. A resolved
RTSTRUCT->RTPLAN->RTDOSE chain whose planning image is an MR sharing the RT frame of reference
must grade OK, with no "retrieve the CT" remediation. A chain with NO planning image at all
(neither CT nor MR) should still warn.

TDD: written before the MR-aware change to build_rt_rollup.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery.rt_integrity import build_rt_integrity, build_rt_rollup  # noqa: E402

_DEFAULTS = dict(
    patient_id="P1", study_uid="S1", series_uid="", modality="", sop_instance_uid="",
    frame_of_reference="FOR1", roi_names=[], ref_struct_uids=[], ref_plan_uids=[],
    study_date="20200101", path="", dose_units="", dose_sum_type="", n_fractions=None,
)


def _obj(**kw):
    row = dict(_DEFAULTS)
    row.update(kw)
    return row


def _resolved_chain(planning=None):
    """A fully-resolved RTSTRUCT->RTPLAN->RTDOSE chain with GTV/CTV/PTV, one frame of reference.

    ``planning`` optionally prepends a planning image object of that modality (e.g. "MR"/"CT").
    """
    rows = []
    if planning is not None:
        rows.append(_obj(modality=planning, sop_instance_uid=f"{planning}1"))
    rows += [
        _obj(modality="RTSTRUCT", sop_instance_uid="ST1", roi_names=["GTV", "CTV", "PTV"]),
        _obj(modality="RTPLAN", sop_instance_uid="PL1", ref_struct_uids=["ST1"]),
        _obj(modality="RTDOSE", sop_instance_uid="DO1", ref_plan_uids=["PL1"]),
    ]
    return pd.DataFrame(rows)


def test_mr_planned_chain_grades_ok_without_ct():
    row = build_rt_rollup(_resolved_chain(planning="MR")).iloc[0]
    assert row["rt_status"] == "OK", f"MR-planned chain should be OK, got {row['rt_status']} ({row['reason']})"
    assert "planning CT" not in row["reason"]
    assert "planning image" not in row["action"]  # no "fetch a CT" instruction on an MR plan


def test_image_less_chain_still_warns_missing_planning_image():
    row = build_rt_rollup(_resolved_chain(planning=None)).iloc[0]
    assert row["rt_status"] == "WARN"
    assert "planning image" in row["reason"]


def test_ct_planned_chain_still_ok():
    # Regression guard: the MR-aware change must not disturb the CT path.
    row = build_rt_rollup(_resolved_chain(planning="CT")).iloc[0]
    assert row["rt_status"] == "OK", f"got {row['rt_status']} ({row['reason']})"
    assert "planning" not in row["reason"]


def test_per_study_mr_planned_not_flagged_missing_ct():
    # The per-study drill-down (build_rt_integrity) must also treat an in-study MR as the
    # planning image, not emit a MISSING_CT warning that caps the study to WARN.
    row = build_rt_integrity(_resolved_chain(planning="MR")).iloc[0]
    assert "MISSING_CT" not in row["findings"]
    assert row["rt_status"] == "OK", f"{row['rt_status']} :: {row['findings']}"
