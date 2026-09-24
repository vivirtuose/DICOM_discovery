"""Analysis-ready exports: a missing exam must be distinguishable from one not yet due."""
from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from DICOM_discovery.cohort_export import (  # noqa: E402
    PRESENCE_COLUMNS,
    extraction_date,
    missing_due,
    patient_timing,
    presence_long,
)
from DICOM_discovery.completeness import Protocol, build_completeness  # noqa: E402

PROTO = Protocol(timepoints=["baseline", "M3", "M12"],
                 required={"baseline": ["CT"], "M3": ["MR"], "M12": ["MR"]},
                 offsets={"baseline": 0, "M3": 90, "M12": 360}, tolerance_days=30,
                 name="tiny")


def _row(pid, study, date, mod, series=None, n=1, src="dicom"):
    return {"path": f"{pid}/{study}/{mod}", "source_root": "/r", "patient_id": pid,
            "patient_id_source": src, "study_uid": study, "study_date": date,
            "series_uid": series or f"{study}.{mod}", "modality": mod,
            "sop_class_uid": "", "sop_instance_uid": "", "frame_of_reference": "F",
            "roi_names": None, "ref_struct_uids": None, "ref_plan_uids": None,
            "dose_units": "", "dose_sum_type": "", "n_fractions": None, "n_instances": n}


def _cohort():
    table = pd.DataFrame([
        # OLD: included 2020, M3 done late (day 110), M12 never done -> truly missing.
        _row("OLD", "o1", "20200101", "CT", n=180),
        _row("OLD", "o2", "20200420", "MR", n=24),
        _row("OLD", "o2", "20200420", "MR", series="o2.MR.b", n=20),
        # NEW: included two months before extraction - M3 and M12 cannot have happened yet.
        _row("NEW", "n1", "20240801", "CT", n=150),
        # UND: no readable date anywhere -> cannot be placed.
        _row("UND", "u1", "", "CT"),
    ])
    _s, _h, comp_long = build_completeness(table, PROTO)
    return table, comp_long, {"generated_utc": "2024-10-01T10:00:00Z"}


def _rec(records, pid, tp, mod):
    return next(r for r in records if (r["patient_id"], r["timepoint"], r["modality"]) == (pid, tp, mod))


def test_extraction_date_is_the_censoring_date():
    assert extraction_date({"generated_utc": "2024-10-01T10:00:00Z"}).isoformat() == "2024-10-01"
    assert extraction_date({}) is None


def test_a_visit_not_yet_due_is_not_counted_as_missing():
    """The bias the first export carried: NEW's M12 MRI is MISSING in the grading, but its
    window closes in 2025 - after extraction. Only OLD's M12 is truly missing."""
    table, comp_long, manifest = _cohort()
    records = presence_long(table, comp_long, PROTO, manifest)
    assert _rec(records, "NEW", "M12", "MR")["state"] == "MISSING"
    assert _rec(records, "NEW", "M12", "MR")["window_closed"] is False
    assert _rec(records, "OLD", "M12", "MR")["window_closed"] is True
    assert missing_due(records) == {"OLD": 1}


def test_each_cell_carries_its_timing_and_its_quantity():
    table, comp_long, manifest = _cohort()
    r = _rec(presence_long(table, comp_long, PROTO, manifest), "OLD", "M3", "MR")
    assert (r["present"], r["study_date"], r["days_from_baseline"], r["days_from_target"]) == (
        1, "2020-04-20", 110, 20)
    assert (r["n_studies"], r["n_series"], r["n_files"]) == (1, 2, 44)
    assert (r["window_start_day"], r["window_end_day"]) == (60, 120)
    assert (r["protocol"], r["tolerance_days"], r["extraction_date"]) == ("tiny", 30, "2024-10-01")


def test_an_unplaceable_patient_has_unknown_presence_not_zero():
    """Absence cannot be asserted for a patient no exam could be dated for: a 0 there would
    enter a presence model as a real missing exam."""
    table, comp_long, manifest = _cohort()
    und = [r for r in presence_long(table, comp_long, PROTO, manifest) if r["patient_id"] == "UND"]
    assert und and all(r["present"] is None and r["state"] == "UNMAPPED" for r in und)


def test_the_long_table_loads_as_typed_columns():
    table, comp_long, manifest = _cohort()
    df = pd.DataFrame(presence_long(table, comp_long, PROTO, manifest), columns=PRESENCE_COLUMNS)
    assert not df.duplicated(["patient_id", "timepoint", "modality"]).any()
    assert set(df["present"].dropna().unique()) <= {0, 1}
    assert df.loc[df["state"] == "MISSING", "present"].eq(0).all()
    assert df.loc[df["state"] == "PRESENT", "present"].eq(1).all()


def test_patient_timing_counts_dates_and_data_quality():
    table, _c, _m = _cohort()
    t = patient_timing(table, PROTO)
    assert (t["OLD"]["baseline_date"], t["OLD"]["last_study_date"], t["OLD"]["followup_days"]) == (
        "2020-01-01", "2020-04-20", 110)
    assert t["UND"]["n_studies_undated"] == 1 and t["UND"]["baseline_date"] is None
    assert t["OLD"]["patient_id_source"] == "dicom"
