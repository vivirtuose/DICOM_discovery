"""The overview tab: what the tool is, what the cohort is, drawn before any verdict.

Every assertion here defends one of the rules in report_overview's docstring — a number is
labelled as the thing it actually counts, and a chart is correct at rest, before any
animation has run.
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from DICOM_discovery.report_overview import (  # noqa: E402
    _RING_C,
    cohort_overview,
    overview_script,
    overview_section_html,
    overview_styles,
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


def _comp_long(*rows: dict) -> pd.DataFrame:
    base = {"timepoint": "baseline", "modality": "CT", "expected": True, "state": "PRESENT"}
    return pd.DataFrame([{**base, **r} for r in rows])


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# The acquisition histogram
# --------------------------------------------------------------------------- #
def test_a_month_with_no_study_is_drawn_as_an_empty_month():
    """A gap in recruitment is a fact about the cohort. Emitting only the months that have
    studies would draw a three-month hole as continuous activity."""
    ov = cohort_overview(_table(
        {"patient_id": "P1", "study_uid": "a", "study_date": "20240101"},
        {"patient_id": "P1", "study_uid": "b", "study_date": "20240401"},
    ), {})
    labels = [b["label"] for b in ov["time_buckets"]]
    assert labels == ["2024-01", "2024-02", "2024-03", "2024-04"]
    assert [b["n_studies"] for b in ov["time_buckets"]] == [1, 0, 0, 1]


def test_a_long_cohort_switches_to_quarters_instead_of_drawing_a_comb():
    ov = cohort_overview(_table(
        {"patient_id": "P1", "study_uid": "a", "study_date": "20180101"},
        {"patient_id": "P1", "study_uid": "b", "study_date": "20240101"},
    ), {})
    assert ov["time_bucket_unit"] == "quarter"
    assert ov["time_buckets"][0]["label"] == "2018 Q1"
    assert ov["time_buckets"][-1]["label"] == "2024 Q1"


def test_a_cohort_with_no_readable_date_says_so_instead_of_drawing_nothing():
    ov = cohort_overview(_table({"patient_id": "P1", "study_date": ""}), {})
    assert ov["time_buckets"] == []
    assert "no acquisition history" in overview_section_html(ov)


# --------------------------------------------------------------------------- #
# The protocol ring
# --------------------------------------------------------------------------- #
def test_the_protocol_split_buckets_are_mutually_exclusive():
    """Complete, incomplete and ungradeable are three different claims. A patient counted in
    two of them would make the ring sum past the cohort."""
    comp = _comp_long(
        {"patient": "A", "state": "PRESENT"},
        {"patient": "A", "timepoint": "M3", "modality": "MR", "state": "PRESENT"},
        {"patient": "B", "state": "MISSING"},
        {"patient": "B", "timepoint": "M3", "modality": "MR", "state": "PRESENT"},
        {"patient": "C", "state": "UNMAPPED"},
        {"patient": "C", "timepoint": "M3", "modality": "MR", "state": "UNMAPPED"},
        {"patient": "D", "state": "NA", "expected": False},
    )
    split = cohort_overview(_table({"patient_id": "P1"}), {}, comp)["protocol_split"]
    assert split["n_complete"] == 1          # A
    assert split["n_incomplete"] == 1        # B
    assert split["n_ungradeable"] == 1       # C
    assert split["n_nothing_expected"] == 1  # D
    assert split["n_graded"] == 2


def test_a_patient_with_one_unmapped_cell_is_not_called_ungradeable():
    """Only a patient whose every cell is UNMAPPED cannot be placed. One odd timepoint beside
    real verdicts still has a real verdict."""
    comp = _comp_long(
        {"patient": "A", "state": "PRESENT"},
        {"patient": "A", "timepoint": "M3", "modality": "MR", "state": "UNMAPPED"},
    )
    split = cohort_overview(_table({"patient_id": "P1"}), {}, comp)["protocol_split"]
    assert (split["n_ungradeable"], split["n_complete"]) == (0, 1)


def test_the_ring_arcs_cover_the_circle_exactly_once():
    """Each arc is drawn by dash length and offset. Arcs that do not sum to the circumference
    leave a wedge of empty ring that reads as a fourth, unlabelled category."""
    comp = _comp_long(
        {"patient": "A", "state": "PRESENT"},
        {"patient": "B", "state": "MISSING"},
        {"patient": "C", "state": "UNMAPPED"},
    )
    html = overview_section_html(cohort_overview(_table({"patient_id": "P1"}), {}, comp))
    lens = [float(chunk.split("--len:")[1].split(";")[0])
            for chunk in html.split("class='ovring-seg'")[1:]]
    assert len(lens) == 3
    # Tolerance is the rendering precision, not an epsilon: each arc is written to two
    # decimals, so three arcs can drift half a hundredth each and still tile the ring.
    assert sum(lens) == pytest.approx(_RING_C, abs=0.05)


# --------------------------------------------------------------------------- #
# The page as a whole
# --------------------------------------------------------------------------- #
def test_the_page_opens_by_saying_what_the_tool_does():
    ov = cohort_overview(_table({"patient_id": "P1"}), {"root": "/nas/cohort"})
    html = overview_section_html(ov)
    assert html.index("What DICOM_discovery does") < html.index("What was scanned")
    assert html.count("class='ovfeat'") == 4
    assert "headers only" in html and "not a medical device" in html
    assert "/nas/cohort" in html


def test_every_animated_figure_already_contains_its_final_value():
    """The animation may never run - JS off, motion reduced, a printed copy. It is not allowed
    to be the thing that writes the number, so the markup carries the final text and the
    script only replaces it with itself."""
    ov = cohort_overview(_table(
        {"patient_id": "P1", "series_uid": "s1", "n_instances": 1234},
    ), {"n_files_seen": 1234})
    html = overview_section_html(ov)
    assert "1 234" in html
    assert "data-count='1234'" in html


def test_reduced_motion_leaves_every_chart_at_its_final_geometry():
    """Switching the animation off must not leave a bar at width 0 - the resting state has to
    be the correct state, not merely the state the animation ends in."""
    css = overview_styles()
    block = css.split("prefers-reduced-motion")[1]
    assert "width:var(--pct)" in block
    assert "height:var(--h)" in block
    assert "animation:none" in block


def test_the_script_refuses_to_animate_a_hidden_tab():
    """The overview lives in a tab panel. A one-shot count-up fired while the panel is hidden
    would be over before anyone switched to it."""
    assert "offsetParent" in overview_script()


def test_empty_cohort_says_so_instead_of_rendering_zeroes():
    """Zeroes across a dashboard read as "a clean cohort". Nothing indexed is not a clean
    cohort - it is a scan that found nothing, usually a wrong path."""
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


# --------------------------------------------------------------------------- #
# The RT verdict pie
# --------------------------------------------------------------------------- #
def test_the_verdict_pie_draws_the_integrity_tab_s_own_counts():
    """The overview borrows the counts, it does not compute a second opinion about them."""
    ov = cohort_overview(_table({"patient_id": "P1"}), {},
                         verdicts={"OK": 6, "WARN": 4, "INCOMPLETE": 2, "NO_RT": 0})
    assert ov["verdicts"] == {"OK": 6, "WARN": 4, "INCOMPLETE": 2, "NO_RT": 0}
    html = overview_section_html(ov)
    assert "RT verdicts across the cohort" in html
    assert html.count("class='ovpie-sl'") == 3, "a zero-count verdict must not draw a slice"
    assert "OK: 6 of 12 patients (50%)" in html


def test_a_single_verdict_is_drawn_as_a_circle_not_a_degenerate_wedge():
    """A 360-degree arc collapses to a point, which draws nothing at all. One verdict for the
    whole cohort has to become a circle."""
    html = overview_section_html(cohort_overview(
        _table({"patient_id": "P1"}), {}, verdicts={"OK": 9}))
    assert "<circle class='ovpie-sl'" in html
    assert "<path class='ovpie-sl'" not in html


def test_the_pie_wedges_meet_without_leaving_a_gap():
    """Each wedge starts where the last ended. A wedge that does not close leaves a sliver of
    empty pie that reads as an unlabelled fifth verdict."""
    import re
    html = overview_section_html(cohort_overview(
        _table({"patient_id": "P1"}), {}, verdicts={"OK": 3, "WARN": 2, "INCOMPLETE": 1}))
    paths = re.findall(r"<path class='ovpie-sl' d='([^']*)'", html)
    assert len(paths) == 3
    ends = [re.search(r"1 (-?[\d.]+) (-?[\d.]+) Z$", d).groups() for d in paths]
    starts = [re.search(r"^M [\d.]+ [\d.]+ L (-?[\d.]+) (-?[\d.]+)", d).groups() for d in paths]
    for i in range(1, 3):
        assert starts[i] == ends[i - 1], "wedge does not start where the previous one ended"
    # The last wedge closes the circle: it ends where the first one began.
    assert ends[-1] == starts[0]


def test_no_pie_is_drawn_when_no_verdict_was_supplied():
    """The standalone overview can be rendered without the integrity engine having run. An
    empty pie of zeroes would claim a cohort with no verdicts at all."""
    html = overview_section_html(cohort_overview(_table({"patient_id": "P1"}), {}))
    assert "ovpie" not in html


# --------------------------------------------------------------------------- #
# Typography and measure
# --------------------------------------------------------------------------- #
def test_no_block_caps_its_own_text_short_of_the_card_edge():
    """A measure set in `ch` at 12px capped the lede near 500px, so every block ended in a
    half-page of white that read as missing content. The card sets the measure now."""
    css = overview_styles()
    lede = css.split(".ovlede{")[1].split("}")[0]
    assert "max-width" not in lede


def test_block_headings_are_bigger_than_body_text_and_carry_colour():
    """Five blocks whose headings looked like bold body text read as one long document with
    no landmarks."""
    css = overview_styles()
    heading = css.split(".ovh{")[1].split("}")[0]
    assert "font-size:17px" in heading
    assert "var(--accent)" in heading
