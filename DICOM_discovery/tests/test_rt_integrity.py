"""Tests for the RT chain integrity engine, driven by the synthetic cohort.

The synthetic cohort carries its own ground truth (expected status + expected finding
codes per patient), so these tests double as the "case -> expected -> detected"
validation table documented in the README.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT / "synth"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd  # noqa: E402

from DICOM_discovery import (  # noqa: E402
    Confidence,
    RTObject,
    Severity,
    Status,
    assess_patient,
    build_rt_rollup,
    read_rt_object,
    rtobjects_from_table,
    scan_rt_cohort,
)


def _rt_table(rows):
    """Minimal canonical-table rows for rollup tests (missing cols default sensibly)."""
    base = dict(study_uid="S", study_date="20200101", series_uid="", frame_of_reference="F",
                sop_instance_uid="", roi_names=[], ref_struct_uids=[], ref_plan_uids=[],
                dose_units="", dose_sum_type="", n_fractions=None)
    return pd.DataFrame([{**base, **r} for r in rows])

PATIENTS = ["P001", "P002", "P003", "P004", "P005", "P006", "P007"]


def _row(df, pid):
    sub = df[df["patient_id"] == pid]
    assert len(sub) == 1, f"expected exactly one row for {pid}"
    return sub.iloc[0]


@pytest.mark.parametrize("pid", PATIENTS)
def test_status_matches_ground_truth(cohort, pid):
    df, _scan, truth = cohort
    assert _row(df, pid)["rt_status"] == truth[pid].expected_status


@pytest.mark.parametrize("pid", PATIENTS)
def test_expected_finding_codes_present(cohort, pid):
    df, _scan, truth = cohort
    findings = _row(df, pid)["findings"]
    for code in truth[pid].expected_codes:
        assert code in findings, f"{pid}: expected finding {code} missing from: {findings}"


def test_p001_is_clean(cohort):
    """A nominal complete chain must be OK with zero errors and zero warnings."""
    df, _scan, _truth = cohort
    r = _row(df, "P001")
    assert r["rt_status"] == "OK"
    assert r["n_errors"] == 0 and r["n_warnings"] == 0
    assert r["roi_GTV"] and r["roi_CTV"] and r["roi_PTV"]


def test_p002_no_multi_struct_false_positive(cohort):
    """Regression test for the v1 false positive.

    With two RTSTRUCT re-exports and a plan that links the second one, v1 flagged
    PLAN_STRUCT_LINK_BROKEN. v2 resolves the link against all present structs, so the
    patient must be OK with no broken-link finding.
    """
    df, _scan, _truth = cohort
    r = _row(df, "P002")
    assert r["rt_status"] == "OK"
    assert bool(r["plan_links_struct"])
    assert "PLAN_STRUCT_LINK_BROKEN" not in r["findings"]


def test_p003_broken_plan_link_is_high_confidence_error(cohort):
    """The genuine dangling reference must surface as a HIGH-confidence ERROR."""
    _df, idx, _truth = cohort
    objs = [o for o in rtobjects_from_table(idx.table) if o.patient_id == "P003"]
    status, findings = assess_patient("P003", objs)
    broken = [f for f in findings if f.code == "PLAN_STRUCT_LINK_BROKEN"]
    assert status == Status.WARN
    assert broken and broken[0].severity == Severity.ERROR
    assert broken[0].confidence == Confidence.HIGH


def test_for_ct_mismatch_is_only_heuristic():
    """CT-based FoR mismatch must never be a hard error (planning-CT guess is uncertain)."""
    struct = RTObject(path="s", patient_id="X", modality="RTSTRUCT",
                      sop_instance_uid="S1", frame_of_reference="FOR_RT",
                      roi_names=["GTV", "CTV", "PTV"])
    plan = RTObject(path="p", patient_id="X", modality="RTPLAN",
                    sop_instance_uid="P1", frame_of_reference="FOR_RT",
                    ref_struct_uids=["S1"])
    dose = RTObject(path="d", patient_id="X", modality="RTDOSE",
                    sop_instance_uid="D1", frame_of_reference="FOR_RT",
                    ref_plan_uids=["P1"])
    ct = RTObject(path="c", patient_id="X", modality="CT", frame_of_reference="FOR_OTHER_CT")
    status, findings = assess_patient("X", [struct, plan, dose, ct])
    mism = [f for f in findings if f.code == "FOR_CT_MISMATCH"]
    assert mism and mism[0].severity == Severity.INFO
    assert mism[0].confidence == Confidence.HEURISTIC
    # The chain itself is sound, so an INFO-only hint must not drag it below OK.
    assert status == Status.OK


def test_unreadable_file_is_reported_not_dropped(tmp_path):
    """A corrupt/non-DICOM file must be captured as an error, not silently lost."""
    patient_dir = tmp_path / "P999"
    patient_dir.mkdir()
    (patient_dir / "garbage.dcm").write_bytes(b"this is not a dicom file")
    scan = scan_rt_cohort(str(tmp_path))
    assert any(e["path"].endswith("garbage.dcm") for e in scan.errors)
    assert read_rt_object(str(patient_dir / "garbage.dcm")) is None


# --------------------------------------------------------------------------- #
# v0.4: NOT_RT scoping, patient rollup, fragmentation, regression guard
# --------------------------------------------------------------------------- #
def test_non_rt_study_is_not_assessed_as_incomplete():
    """A study with no RT object (e.g. follow-up MR) must be NOT_RT, not INCOMPLETE."""
    mr = RTObject(path="m", patient_id="X", modality="MR", study_uid="S", frame_of_reference="F")
    ct = RTObject(path="c", patient_id="X", modality="CT", study_uid="S", frame_of_reference="F")
    status, findings = assess_patient("X", [mr, ct])
    assert status == Status.NOT_RT
    assert findings == []


def test_rollup_no_rt_patient():
    df = _rt_table([{"patient_id": "X", "modality": "MR"}, {"patient_id": "X", "modality": "MR", "study_uid": "S2"}])
    roll = build_rt_rollup(df)
    assert roll.iloc[0]["rt_status"] == "NO_RT"


def test_rollup_complete_single_study_is_ok():
    df = _rt_table([
        {"patient_id": "X", "modality": "CT"},
        {"patient_id": "X", "modality": "RTSTRUCT", "sop_instance_uid": "s1", "roi_names": ["GTV", "CTV", "PTV"]},
        {"patient_id": "X", "modality": "RTPLAN", "sop_instance_uid": "p1", "ref_struct_uids": ["s1"]},
        {"patient_id": "X", "modality": "RTDOSE", "sop_instance_uid": "d1", "ref_plan_uids": ["p1"]},
    ])
    r = build_rt_rollup(df).iloc[0]
    assert r["rt_status"] == "OK" and not r["fragmented"]


def test_rollup_fragmented_chain_resolves_to_ok():
    """Chain split across two studies but links resolve -> OK + FRAGMENTED (not INCOMPLETE)."""
    df = _rt_table([
        {"patient_id": "X", "study_uid": "A", "modality": "CT"},
        {"patient_id": "X", "study_uid": "A", "modality": "RTSTRUCT", "sop_instance_uid": "s1", "roi_names": ["GTV", "CTV", "PTV"]},
        {"patient_id": "X", "study_uid": "A", "modality": "RTPLAN", "sop_instance_uid": "p1", "ref_struct_uids": ["s1"]},
        {"patient_id": "X", "study_uid": "B", "modality": "RTDOSE", "sop_instance_uid": "d1", "ref_plan_uids": ["p1"]},
    ])
    r = build_rt_rollup(df).iloc[0]
    assert r["rt_status"] == "OK" and r["fragmented"]


def test_rollup_missing_dose_is_incomplete_named():
    """IC 034 shape: struct+plan present and linked, but RTDOSE truly absent -> INCOMPLETE."""
    df = _rt_table([
        {"patient_id": "X", "study_uid": "A", "modality": "CT"},
        {"patient_id": "X", "study_uid": "A", "modality": "RTSTRUCT", "sop_instance_uid": "s1", "roi_names": ["GTV", "CTV", "PTV"]},
        {"patient_id": "X", "study_uid": "B", "modality": "RTPLAN", "sop_instance_uid": "p1", "ref_struct_uids": ["s1"]},
    ])
    r = build_rt_rollup(df).iloc[0]
    assert r["rt_status"] == "INCOMPLETE"
    assert "RTDOSE" in r["reason"] and r["fragmented"]


def test_rollup_unlinked_objects_not_false_ok():
    """All object types present but the plan->struct link does not resolve -> not OK."""
    df = _rt_table([
        {"patient_id": "X", "modality": "CT"},
        {"patient_id": "X", "modality": "RTSTRUCT", "sop_instance_uid": "s1", "roi_names": ["GTV", "CTV", "PTV"]},
        {"patient_id": "X", "modality": "RTPLAN", "sop_instance_uid": "p1", "ref_struct_uids": ["OTHER"]},
        {"patient_id": "X", "modality": "RTDOSE", "sop_instance_uid": "d1", "ref_plan_uids": ["p1"]},
    ])
    r = build_rt_rollup(df).iloc[0]
    assert r["rt_status"] != "OK"
    assert "resolve" in r["reason"].lower()


def test_synthetic_cohort_has_no_spurious_for_inconsistent(cohort):
    """Regression guard: only P005 (intentional within-study FoR clash) may be FOR_INCONSISTENT."""
    df, _idx, _truth = cohort
    flagged = set(df[df["findings"].str.contains("FOR_INCONSISTENT", na=False)]["patient_id"])
    assert flagged <= {"P005"}
