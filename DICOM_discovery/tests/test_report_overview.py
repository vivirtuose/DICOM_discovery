"""The overview tab: the numbers a first-time reader needs before any verdict.

Every assertion here defends one of two rules stated in report_overview's docstring — that a
number is labelled as the thing it actually counts, and that no figure ships without the
sentence explaining it.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from DICOM_discovery.report_overview import (  # noqa: E402
    cohort_overview,
    overview_section_html,
)

_DEFAULTS = {
    "path": "x.dcm", "source_root": "/root", "patient_id": "P1", "patient_id_source": "tag",
    "study_uid": "1.1", "study_date": "20240101", "series_uid": "2.1", "modality": "CT",
    "sop_class_uid": "", "sop_instance_uid": "", "frame_of_reference": "FOR1",
    "roi_names": None, "ref_struct_uids": None, "ref_plan_uids": None,
    "dose_units": "", "dose_sum_type": "", "n_fractions": None, "n_instances": 1,
}


def _table(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame([{**_DEFAULTS, **r} for r in rows])


def test_files_and_rows_are_reported_as_two_different_facts():
    """A 200-slice CT is one row and two hundred files. Reporting one number under one label
    is how a cohort of 4 000 files gets described as a cohort of 20."""
    ov = cohort_overview(_table(
        {"patient_id": "P1", "series_uid": "s1", "modality": "CT", "n_instances": 200},
        {"patient_id": "P1", "series_uid": "", "modality": "RTPLAN", "n_instances": 1},
    ), {})
    assert ov["n_rows"] == 2
    assert ov["n_files"] == 201
    ct = next(m for m in ov["modalities"] if m["modality"] == "CT")
    assert (ct["n_files"], ct["n_series"]) == (200, 1)


def test_undated_studies_are_counted_and_never_pass_as_dated():
    """An unreadable StudyDate is the most common reason a complete patient reads as
    ungradeable, so the front page has to say how many there are."""
    ov = cohort_overview(_table(
        {"patient_id": "P1", "study_uid": "a", "study_date": "20240101"},
        {"patient_id": "P1", "study_uid": "b", "study_date": ""},
        {"patient_id": "P2", "study_uid": "c", "study_date": "not-a-date"},
    ), {})
    assert ov["n_undated_studies"] == 2
    assert ov["n_dated_studies"] == 1
    assert ov["date_min"] == ov["date_max"] == "2024-01-01"


def test_single_study_patients_are_named_and_excluded_from_follow_up():
    """A patient with one study has no follow-up to be missing. Folding their zero-day span
    into the median would report the cohort as barely followed for a reason that is not about
    follow-up at all."""
    ov = cohort_overview(_table(
        {"patient_id": "P1", "study_uid": "a", "study_date": "20240101"},
        {"patient_id": "P1", "study_uid": "b", "study_date": "20240401"},   # 91 days
        {"patient_id": "P2", "study_uid": "c", "study_date": "20240101"},   # single study
    ), {})
    assert ov["n_single_study_patients"] == 1
    assert ov["followup_days"]["median"] == 91
    assert ov["studies_per_patient"] == {"min": 1, "median": 1.5, "max": 2}


def test_unplaceable_patients_are_borrowed_from_the_completeness_engine():
    """Two definitions of "ungradeable" on one page is how the tabs end up contradicting each
    other, so this number is taken from comp_long rather than recomputed."""
    comp_long = pd.DataFrame([
        {"patient": "P1", "timepoint": "baseline", "modality": "CT", "state": "UNMAPPED"},
        {"patient": "P1", "timepoint": "M3", "modality": "MR", "state": "UNMAPPED"},
        {"patient": "P2", "timepoint": "baseline", "modality": "CT", "state": "PRESENT"},
        {"patient": "P2", "timepoint": "M3", "modality": "MR", "state": "UNMAPPED"},
    ])
    ov = cohort_overview(_table({"patient_id": "P1"}, {"patient_id": "P2"}), {}, comp_long)
    # P2 has one UNMAPPED cell but is still gradeable elsewhere — only P1 cannot be placed.
    assert ov["n_unplaceable_patients"] == 1


def test_identifier_source_is_surfaced_because_every_other_tab_rests_on_it():
    ov = cohort_overview(_table(
        {"patient_id": "P1", "patient_id_source": "tag"},
        {"patient_id": "P2", "patient_id_source": "folder"},
        {"patient_id": "P2", "patient_id_source": "folder", "series_uid": "other"},
    ), {})
    assert {s["source"]: s["n_patients"] for s in ov["id_sources"]} == {"tag": 1, "folder": 1}
    assert "from the folder name" in overview_section_html(ov)


def test_patients_split_across_frames_of_reference_are_flagged():
    """An RT chain can be internally consistent and still not be registered."""
    ov = cohort_overview(_table(
        {"patient_id": "P1", "frame_of_reference": "A", "series_uid": "s1"},
        {"patient_id": "P1", "frame_of_reference": "B", "series_uid": "s2"},
        {"patient_id": "P2", "frame_of_reference": "A", "series_uid": "s3"},
    ), {})
    assert ov["n_multi_frame_of_reference_patients"] == 1


def test_every_rendered_figure_carries_its_explanation():
    """The complaint this tab answers was that a first-time reader understands nothing. A bare
    number with a terse label is exactly what produced that, so the count of figures and the
    count of explanations must match."""
    ov = cohort_overview(_table(
        {"patient_id": "P1", "study_uid": "a", "study_date": "20240101", "series_uid": "s1"},
        {"patient_id": "P1", "study_uid": "b", "study_date": "20240401", "series_uid": "s2"},
        {"patient_id": "P2", "study_uid": "c", "study_date": "", "series_uid": "s3"},
    ), {"n_files_seen": 9, "n_unreadable": 1})
    html = overview_section_html(ov)
    assert html.count("class='ovnum'") == html.count("class='ovnote'") > 6
    assert "class='ovlab'" in html


def test_empty_cohort_says_so_instead_of_rendering_zeroes():
    """Zeroes across a dashboard read as "a clean cohort". Nothing indexed is not a clean
    cohort — it is a scan that found nothing, usually a wrong path."""
    ov = cohort_overview(pd.DataFrame(), {"root": "/nowhere"})
    assert ov["empty"] is True
    html = overview_section_html(ov)
    assert "no cohort" in html and "Check the path" in html


@pytest.mark.parametrize("missing", ["n_files_seen", "n_unreadable", "n_dirs_unreadable"])
def test_a_manifest_without_the_scan_counters_still_renders(missing):
    """The overview is also reachable from a table loaded without its run manifest (a resumed
    or imported index). A missing counter must render as zero, not raise."""
    manifest = {"n_files_seen": 5, "n_unreadable": 0, "n_dirs_unreadable": 0}
    manifest.pop(missing)
    ov = cohort_overview(_table({"patient_id": "P1"}), manifest)
    assert overview_section_html(ov)
