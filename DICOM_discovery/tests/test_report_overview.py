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
    MOD_COLORS,
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
    would be over before anyone switched to it. The visibility test must also work on the
    dial's SVG total, which has no offsetParent - getClientRects covers both."""
    js = overview_script()
    assert "getClientRects" in js
    assert ".offsetParent" not in js, "visibility must not be tested through offsetParent"


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
# The RT verdict dial
# --------------------------------------------------------------------------- #
def _dial(verdicts):
    return overview_section_html(cohort_overview(_table({"patient_id": "P1"}), {},
                                                 verdicts=verdicts))


def _wedges(html):
    """(length, offset, circumference) for each wedge, in drawing order."""
    import re
    out = []
    for style in re.findall(r"<circle class='pw'[^>]*style='([^']*)'", html):
        vals = dict(kv.split(":", 1) for kv in style.split(";") if kv)
        out.append((float(vals["--len"]), float(vals["--off"]), float(vals["--circ"])))
    return out


def test_the_dial_draws_the_integrity_tab_s_own_counts():
    """The overview borrows the counts, it does not compute a second opinion about them."""
    ov = cohort_overview(_table({"patient_id": "P1"}), {},
                         verdicts={"OK": 6, "WARN": 4, "INCOMPLETE": 2, "NO_RT": 0})
    assert ov["verdicts"] == {"OK": 6, "WARN": 4, "INCOMPLETE": 2, "NO_RT": 0}
    html = overview_section_html(ov)
    assert "RT verdicts across the cohort" in html
    assert html.count("<circle class='pw'") == 3, "a zero-count verdict must not draw a wedge"
    assert "OK: 6 of 12 patients (50%)" in html
    assert "data-count='12'" in html, "the hub must carry the cohort total"


def test_the_wedges_tile_the_disc_exactly_once():
    """Each wedge starts where the last one ended and together they close the circle. A gap
    would read as an unlabelled fifth verdict; an overlap would hide part of one."""
    wedges = _wedges(_dial({"OK": 3, "WARN": 2, "INCOMPLETE": 1, "NO_RT": 1}))
    assert len(wedges) == 4
    circ = wedges[0][2]
    running = 0.0
    for length, offset, _ in wedges:
        assert offset == pytest.approx(-running, abs=0.01), "wedge does not start where the last ended"
        running += length
    assert running == pytest.approx(circ, abs=0.02), "the wedges do not close the circle"


def test_a_single_verdict_fills_the_whole_disc():
    """One verdict for the whole cohort is a full disc. The stroke technique needs no special
    case for it - but a regression to path arcs would draw a 360-degree arc as nothing."""
    html = _dial({"OK": 9})
    (length, offset, circ), = _wedges(html)
    assert length == pytest.approx(circ, abs=0.01) and offset == 0
    assert "class='psep'" not in html, "one verdict has no boundary to mark"


def test_each_wedge_is_swept_while_the_scanner_crosses_it():
    """The motion is one idea: the arm turns once and paints each wedge as it passes. A wedge
    whose sweep starts before the arm reaches it, or lasts longer than the arm spends on it,
    breaks the illusion that the arm is doing the painting."""
    import re

    from DICOM_discovery.report_overview import _PIE_SWEEP_S, _PIE_SWEEP_T0
    html = _dial({"OK": 5, "WARN": 3, "INCOMPLETE": 2})
    starts = [float(x) for x in re.findall(r"--t0:([\d.]+)s;--d", html)]
    durs = [float(x) for x in re.findall(r"--d:([\d.]+)s'", html)]
    assert len(starts) == len(durs) == 3
    assert starts[0] == pytest.approx(_PIE_SWEEP_T0, abs=1e-3)
    for i in range(1, len(starts)):
        assert starts[i] == pytest.approx(starts[i - 1] + durs[i - 1], abs=2e-3)
    assert sum(durs) == pytest.approx(_PIE_SWEEP_S, abs=5e-3)


def test_legend_and_wedges_share_one_key_so_hover_can_link_them():
    """Hovering a legend row singles its wedge out and vice versa; that only works if both
    carry the same data-v, and every wedge has a row."""
    import re
    html = _dial({"OK": 5, "WARN": 3, "NO_RT": 1})
    wedge_keys = re.findall(r"<circle class='pw' data-v='([^']*)'", html)
    row_keys = re.findall(r"<li class='pl' data-v='([^']*)'", html)
    assert wedge_keys == row_keys == ["OK", "WARN", "NO_RT"]
    assert "tabindex='0'" in html, "the legend must be reachable by keyboard, not only by mouse"
    js = overview_script()
    assert "'has-hot'" in js and "'hot'" in js


def test_the_dial_rests_at_its_final_geometry():
    """Every wedge's resting style is its final arc; the sweep only supplies the empty first
    frame. With animation removed (reduced motion, old browser, print) the pie must be whole."""
    css = overview_styles()
    pw = css.split(".pw{")[1].split("}")[0]
    assert "stroke-dasharray:var(--len) var(--circ)" in pw
    assert "both" in pw, "the sweep must fill backwards so the delay shows an empty wedge"
    sweep = css.split("@keyframes pwsweep{")[1].split("}}")[0]
    assert sweep.startswith("from{") and "to{" not in sweep
    scan = css.split(".pscan{")[1].split("}")[0]
    assert "opacity:0" in scan, "the scanner arm is a transient; at rest it must be gone"


def test_no_dial_is_drawn_when_no_verdict_was_supplied():
    """The standalone overview can be rendered without the integrity engine having run. An
    empty dial of zeroes would claim a cohort with no verdicts at all."""
    html = overview_section_html(cohort_overview(_table({"patient_id": "P1"}), {}))
    assert "ovpie" not in html


def test_one_modality_palette_feeds_both_views():
    """CT must be the same colour on the overview's bars and on the cohort map. They used to be
    two dicts that each claimed to match the other and did not."""
    from DICOM_discovery import report_cohort
    assert report_cohort._MOD_COLORS is MOD_COLORS


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
    no landmarks. The gradient is clipped to a span around the words: painted on the flex
    heading itself it would span the card and the words would only ever show its first few
    percent."""
    css = overview_styles()
    heading = css.split(".ovh{")[1].split("}")[0]
    assert "font-size:18px" in heading
    words = css.split(".ovh-t{")[1].split("}")[0]
    assert "var(--grad)" in words and "background-clip:text" in words
    html = overview_section_html(cohort_overview(_table({"patient_id": "P1"}), {}))
    assert "<h3 class='ovh'><span class='ovh-t'>" in html


def test_cards_wait_for_the_reader_but_never_strand_the_page():
    """The dial sits below the fold; playing its sweep on load means nobody sees it. Cards are
    held until they scroll in - but only when the script can also release them, and never on
    paper, so no path leaves a card paused on its empty first frame."""
    css, js = overview_styles(), overview_script()
    assert ".ovarmed .ovblock:not(.in)" in css and "animation-play-state:paused" in css
    arm = js.index("classList.add('ovarmed')")
    assert js.rfind("'IntersectionObserver' in window", 0, arm) != -1, (
        "the page must only be armed where IntersectionObserver can disarm it")
    assert "@media print" in css
    # The observer can fail to fire (it did, in a backgrounded pane): a hard timer releases
    # every card regardless, so an armed page can never end blank.
    assert "setTimeout(function(){ cards.forEach(release); }" in js
    assert "getBoundingClientRect" in js


def test_the_count_up_has_a_floor_under_it():
    """requestAnimationFrame stops in a background tab; a figure frozen mid-count is a wrong
    number on screen. A timer puts the true value back regardless."""
    js = overview_script()
    assert "el.textContent = final; }, wait + DUR" in js
