"""Tests for the completeness *renderer* (report_completeness.py).

The data layer (``completeness_grid`` / ``completeness_kpis``) is covered by
``test_completeness.py``; here we only assert what a clinician ends up looking at:
no grey "not expected" boxes, worst patients on top, every chip legible without
colour, and a standalone page that still opens on an air-gapped workstation.

Most tests feed hand-built grid/kpi dicts (the Task-1 contract) rather than a real
cohort, so a rendering regression is not masked by a change in the synthetic data.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import (  # noqa: E402
    DEFAULT_PROTOCOL,
    Protocol,
    build_completeness,
    completeness_gaps,
    completeness_grid,
    completeness_kpis,
    render_completeness_map,
)
from DICOM_discovery.report_completeness import (  # noqa: E402
    _csv_rows_js,
    completeness_script,
    completeness_section_html,
    completeness_styles,
)

# --------------------------------------------------------------------------- #
# Fixtures / helpers — the Task-1 dict contract, written out by hand
# --------------------------------------------------------------------------- #

TINY_PROTOCOL = Protocol(
    name="tiny",
    timepoints=["baseline", "M3", "M12"],
    required={"baseline": ["CT", "MR"], "M3": ["MR"], "M12": ["MR"]},
    offsets={"baseline": 0, "M3": 90, "M12": 360},
    tolerance_days=30,
)


def _cell(tp, items):
    """One grid cell: ``items`` is a list of (modality, state) pairs."""
    states = [s for _m, s in items]
    rank = {"NA": 0, "PRESENT": 1, "EXTRA": 2, "UNMAPPED": 3, "MISSING": 4}
    state = max(states, key=lambda s: rank[s], default="NA")
    return {"timepoint": tp, "state": state,
            "items": [{"modality": m, "state": s} for m, s in items]}


def _patient(pid, cells, n_expected, n_present):
    n_missing = n_expected - n_present
    pct = round(100.0 * n_present / n_expected, 1) if n_expected else 100.0
    return {"patient": pid, "n_expected": n_expected, "n_present": n_present,
            "n_missing": n_missing, "pct_complete": pct, "cells": cells}


def _kpis(**over):
    base = {"n_patients": 0, "n_complete": 0, "n_incomplete": 0,
            "pct_complete_mean": 0.0, "worst_gap": None, "n_unmapped": 0}
    base.update(over)
    return base


#: Two patients: L004 misses M3+M12 MR, L001 has everything the protocol asks for.
GRID_TWO = [
    _patient("L004", [
        _cell("baseline", [("CT", "PRESENT"), ("MR", "PRESENT")]),
        _cell("M3", [("MR", "MISSING")]),
        _cell("M12", [("MR", "MISSING")]),
    ], n_expected=4, n_present=2),
    _patient("L001", [
        _cell("baseline", [("CT", "PRESENT"), ("MR", "PRESENT")]),
        _cell("M3", [("MR", "PRESENT")]),
        _cell("M12", [("MR", "PRESENT")]),
    ], n_expected=4, n_present=4),
]
KPIS_TWO = _kpis(n_patients=2, n_complete=1, n_incomplete=1, pct_complete_mean=75.0,
                 worst_gap={"timepoint": "M3", "modality": "MR", "n_patients": 1})
#: The gaps GRID_TWO actually contains, in completeness_gaps' order (tie -> protocol order).
GAPS_TWO = [{"timepoint": "M3", "modality": "MR", "n_patients": 1, "patients": ["L004"]},
            {"timepoint": "M12", "modality": "MR", "n_patients": 1, "patients": ["L004"]}]


def _chips(html_text):
    """Every rendered chip element, as raw HTML strings."""
    return re.findall(r"<span class='chip [^']*'[^>]*>.*?</span></span>", html_text)


# --------------------------------------------------------------------------- #
# 1. "Not expected" is absence of ink
# --------------------------------------------------------------------------- #

def test_a_timepoint_with_nothing_expected_renders_no_chip():
    """A cell with no items must produce an empty table cell, never a placeholder chip.

    Fails if the renderer emits a chip (grey "N/A" box) for cells whose ``items`` list is
    empty — the exact noise the redesign exists to remove.
    """
    grid = [_patient("A", [
        _cell("baseline", [("CT", "PRESENT")]),
        _cell("M3", []),          # nothing expected and nothing observed at M3
        _cell("M12", [("MR", "MISSING")]),
    ], n_expected=2, n_present=1)]
    html_text = completeness_section_html(grid, _kpis(n_patients=1), TINY_PROTOCOL, [])

    assert len(_chips(html_text)) == 2, "an empty cell rendered a chip"
    # The empty cell is still a cell (column alignment survives) but carries no ink.
    assert "<td class='ccell empty'></td>" in html_text


def test_no_cell_carries_a_state_outside_the_four_actionable_ones():
    """Every rendered state must be one of PRESENT / MISSING / EXTRA / UNMAPPED.

    Stronger than pinning the old ``chip-NA`` class name: a grey "not expected" cell
    reintroduced under any other name (NONE, NA2, SKIPPED) fails this, because the whole
    point is that a fifth state must not exist at all, whatever it is called.
    """
    grid = [_patient("A", [
        _cell("baseline", [("CT", "PRESENT"), ("PT", "EXTRA")]),
        _cell("M3", []),
        _cell("M12", [("MR", "MISSING")]),
    ], n_expected=2, n_present=1)]
    html_text = completeness_section_html(grid, _kpis(n_patients=1), TINY_PROTOCOL, [])
    states = set(re.findall(r"data-state='([^']*)'", html_text))
    assert states, "no cell states rendered at all"
    assert states <= {"PRESENT", "MISSING", "EXTRA", "UNMAPPED"}, f"unexpected states: {states}"


def test_no_not_expected_legend_entry_survives():
    """The legend must not reintroduce a grey 'not expected' swatch.

    Fails if someone re-adds an NA legend row: the whole point is that 'not expected'
    is communicated by blankness, explained once in prose, not by a fifth colour.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    swatches = re.findall(r"class='sw sw-([A-Z]+)'", html_text)
    assert swatches, "no legend swatches rendered at all"
    assert "NA" not in swatches
    assert set(swatches) <= {"PRESENT", "MISSING", "EXTRA", "UNMAPPED"}


# --------------------------------------------------------------------------- #
# 2. Worst patients first
# --------------------------------------------------------------------------- #

def test_patients_render_in_the_order_the_data_layer_gave_them():
    """Row order must be the grid's own worst-first order, not re-sorted by the renderer.

    Fails if the renderer sorts rows itself (e.g. alphabetically), which would bury the
    patients that need a re-export at the bottom of a 98-row page.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    order = re.findall(r"<tr class='crow'[^>]*data-pid='([^']+)'", html_text)
    assert order == ["L004", "L001"]


def test_worst_first_order_survives_the_real_cohort(longitudinal):
    """End-to-end: the rendered row order equals completeness_grid's order on real data.

    Fails if the renderer (or the section's DOM assembly) reverses or regroups rows.
    """
    _root, idx = longitudinal
    _state, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    grid = completeness_grid(long_df)
    html_text = completeness_section_html(grid, completeness_kpis(long_df), DEFAULT_PROTOCOL,
                                          completeness_gaps(long_df))
    order = re.findall(r"<tr class='crow'[^>]*data-pid='([^']+)'", html_text)
    assert order == [p["patient"] for p in grid]
    missing = [p["n_missing"] for p in grid]
    assert missing == sorted(missing, reverse=True), "grid was not worst-first to begin with"


# --------------------------------------------------------------------------- #
# 3. Never colour alone
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("state,word", [
    ("PRESENT", "present"), ("MISSING", "missing"),
    ("EXTRA", "extra"), ("UNMAPPED", "unmapped"),
])
def test_every_chip_states_its_status_in_text_and_aria_label(state, word):
    """A chip must carry its state as a CSS class, as words, and in an aria-label.

    Fails if the state is encoded by colour alone (WCAG 1.4.1) — e.g. if the aria-label
    or the in-text status word is dropped to save bytes.
    """
    grid = [_patient("A", [_cell("M12", [("MR", state)])], n_expected=1, n_present=0)]
    html_text = completeness_section_html(grid, _kpis(n_patients=1), TINY_PROTOCOL, [])
    chips = _chips(html_text)
    assert len(chips) == 1
    chip = chips[0]
    assert f"chip-{state}" in chip, "state missing from the CSS class"
    assert f"aria-label='M12 MR {word}'" in chip, "state missing from the aria-label"
    assert f">{word}</span>" in chip, "state word missing from the chip text"
    assert ">MR<" in chip, "modality label missing from the chip"


def test_every_chip_in_the_real_cohort_is_labelled_with_its_own_state(longitudinal):
    """Each chip's aria-label must read '<timepoint> <modality> <word>' for *its* state.

    Asserting only that the attribute exists would pass a renderer that labelled every chip
    "baseline CT present"; this pins each label against that chip's own data attributes, so
    a mislabelled or copy-pasted branch fails.
    """
    _root, idx = longitudinal
    _state, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    html_text = completeness_section_html(completeness_grid(long_df),
                                          completeness_kpis(long_df), DEFAULT_PROTOCOL,
                                          completeness_gaps(long_df))
    chips = _chips(html_text)
    assert chips, "no chips rendered for the real cohort"
    words = {"PRESENT": "present", "MISSING": "missing",
             "EXTRA": "extra", "UNMAPPED": "unmapped"}
    for chip in chips:
        tp = re.search(r"data-tp='([^']*)'", chip).group(1)
        mod = re.search(r"data-mod='([^']*)'", chip).group(1)
        state = re.search(r"data-state='([^']*)'", chip).group(1)
        assert f"aria-label='{tp} {mod} {words[state]}'" in chip, (
            f"chip labelled inconsistently with its own state: {chip}"
        )


# --------------------------------------------------------------------------- #
# 4. The KPI line
# --------------------------------------------------------------------------- #

def test_kpi_line_names_the_most_missed_timepoint_and_modality():
    """The most-missed gap must be named in plain English, timepoint and modality both.

    This is the line that triggers a batch re-export; fails if it degrades to a bare count.
    """
    kpis = _kpis(n_patients=9, n_complete=4, n_incomplete=5,
                 worst_gap={"timepoint": "M12", "modality": "MR", "n_patients": 3})
    html_text = completeness_section_html(GRID_TWO, kpis, TINY_PROTOCOL, GAPS_TWO)
    assert "M12 - MR" in html_text
    assert "most-missed in 3 patients" in html_text
    assert "Most-missed: M12 - MR (3 patients)" in html_text


def test_kpi_line_uses_singular_wording_for_a_single_patient():
    """'1 patient', not '1 patients' — this text is read by clinicians, not parsers.

    Fails if the plural is hard-coded.
    """
    kpis = _kpis(n_patients=3, n_complete=2,
                 worst_gap={"timepoint": "M3", "modality": "CT", "n_patients": 1})
    html_text = completeness_section_html(GRID_TWO, kpis, TINY_PROTOCOL, GAPS_TWO)
    assert "most-missed in 1 patient" in html_text
    assert "1 patients" not in html_text


def test_kpi_line_omits_the_most_missed_segment_when_nothing_is_missing():
    """A clean cohort must not show an empty 'most-missed' slot.

    Fails if the segment is rendered unconditionally with a None worst_gap.
    """
    kpis = _kpis(n_patients=2, n_complete=2, worst_gap=None)
    html_text = completeness_section_html(GRID_TWO, kpis, TINY_PROTOCOL, GAPS_TWO)
    assert "most-missed" not in html_text
    assert "None" not in html_text


def test_kpi_line_omits_the_unmapped_note_when_nothing_is_unmapped():
    """No unmapped data means no data-quality note at all.

    Fails if a '0 not placed on the timeline' segment is rendered when n_unmapped == 0.
    """
    html_text = completeness_section_html(GRID_TWO, _kpis(n_patients=2, n_unmapped=0),
                                          TINY_PROTOCOL, GAPS_TWO)
    assert "not placed on the timeline" not in html_text


def test_kpi_line_shows_unmapped_as_a_data_quality_note_not_a_patient_failure():
    """Unmapped studies are a protocol-window problem; the wording must say so.

    Fails if the note is phrased as missing patient data (which would send a clinician
    hunting for scans that exist).
    """
    html_text = completeness_section_html(GRID_TWO, _kpis(n_patients=2, n_unmapped=7),
                                          TINY_PROTOCOL, GAPS_TWO)
    assert "not placed on the timeline" in html_text
    assert "Data-quality note" in html_text
    assert "protocol window" in html_text


def test_kpi_headline_counts_complete_patients_over_the_cohort():
    """'n_complete / n_patients patients complete' is the headline.

    Fails if the numerator and denominator are swapped or the label is dropped.
    """
    kpis = _kpis(n_patients=9, n_complete=4, n_incomplete=5)
    grid = [_patient(f"P{i:02d}", [_cell("M3", [("MR", "PRESENT")])],
                     n_expected=1, n_present=1) for i in range(9)]
    html_text = completeness_section_html(grid, kpis, TINY_PROTOCOL, [])
    assert "4 / 9" in html_text
    assert "patients complete" in html_text


# --------------------------------------------------------------------------- #
# 5. The protocol line
# --------------------------------------------------------------------------- #

def test_protocol_line_names_every_timepoint_with_its_window():
    """Every protocol timepoint, its day offset and its tolerance must be stated.

    This is what justifies the word "expected" on the grid; fails if a timepoint the
    protocol defines is silently left out of the explanation.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    assert "Protocol tiny" in html_text
    for tp in TINY_PROTOCOL.timepoints:
        assert f">{tp}</b>" in html_text, f"timepoint {tp} missing from the protocol line"
    assert "day 0 +/-30" in html_text
    assert "day 90 +/-30" in html_text
    assert "day 360 +/-30" in html_text


def test_protocol_line_lists_the_modalities_each_timepoint_requires():
    """The line must say *what* is expected, not only *when*.

    Fails if the required-modality list is dropped, leaving "expected" unjustified.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    assert "CT, MR" in html_text


def test_protocol_line_names_the_default_protocol_timepoints():
    """Same guarantee against the real DEFAULT_PROTOCOL (four timepoints)."""
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, DEFAULT_PROTOCOL, GAPS_TWO)
    for tp in DEFAULT_PROTOCOL.timepoints:
        assert f">{tp}</b>" in html_text


# --------------------------------------------------------------------------- #
# 6. The binding ruling: "nothing expected" is not "100% complete"
# --------------------------------------------------------------------------- #

def test_nothing_expected_row_reads_differently_from_a_complete_row():
    """Two patients with identical numbers (0 missing, 100%) must not render alike.

    Task-1 reports ``n_expected=0, n_missing=0, pct_complete=100.0`` both for a genuinely
    complete patient and for one the protocol asks nothing of. Fails if the renderer shows
    the same "0 / 0 missing" badge for both.
    """
    grid = [
        _patient("COMPLETE", [_cell("M3", [("MR", "PRESENT")])], n_expected=1, n_present=1),
        _patient("VACUOUS", [_cell("M3", [])], n_expected=0, n_present=0),
    ]
    html_text = completeness_section_html(grid, _kpis(n_patients=2, n_complete=2),
                                          TINY_PROTOCOL, [])
    complete_row = re.search(r"<tr class='crow'[^>]*data-pid='COMPLETE'.*?</tr>", html_text, re.S)
    vacuous_row = re.search(r"<tr class='crow'[^>]*data-pid='VACUOUS'.*?</tr>", html_text, re.S)
    assert complete_row and vacuous_row
    assert "nothing expected" in vacuous_row.group(0)
    assert "nothing expected" not in complete_row.group(0)
    assert "0 / 1 missing" in complete_row.group(0)
    assert "0 / 0 missing" not in html_text


def test_nothing_expected_patients_are_kept_out_of_the_complete_headline():
    """A patient with no obligations must not inflate "N patients complete".

    Fails if the renderer prints ``kpis['n_complete']`` verbatim when the grid contains
    zero-expected patients — the inflation the Task-1 report warned about.
    """
    grid = [
        _patient("COMPLETE", [_cell("M3", [("MR", "PRESENT")])], n_expected=1, n_present=1),
        _patient("VACUOUS", [_cell("M3", [])], n_expected=0, n_present=0),
    ]
    # The data layer would say 2 of 2 complete; only one patient actually earned it.
    html_text = completeness_section_html(grid, _kpis(n_patients=2, n_complete=2),
                                          TINY_PROTOCOL, [])
    assert "1 / 2" in html_text
    assert "2 / 2" not in html_text
    assert "nothing expected" in html_text
    assert "1 patient has nothing expected by this protocol" in html_text


# --------------------------------------------------------------------------- #
# 6b. The gap table is the headline; the per-patient grid is the drill-down (Task 2b)
# --------------------------------------------------------------------------- #

def test_the_gap_table_comes_before_the_per_patient_grid():
    """Aggregated gaps lead, per-patient detail follows — that is the whole point of 2b.

    The information that triggers an action is "chase the M6 MR for 3 patients"; the
    per-patient list is how you act on it afterwards. Fails if the two are re-ordered, which
    would put a wall of chips in front of the one sentence that matters.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    assert html_text.index("id='comp-gaps'") < html_text.index("id='comp-grid'")


def test_the_gap_table_names_the_pair_the_count_and_who():
    """Timepoint, modality, how many patients, and which ones — all four in the row.

    Fails if any column is dropped; "3 patients are missing something" is not actionable
    without the pair, and the pair is not actionable without the names.
    """
    gaps = [{"timepoint": "M6", "modality": "MR", "n_patients": 3,
             "patients": ["L002", "L004", "L009"]}]
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, gaps)
    row = re.search(r"<tr class='grow'.*?</tr>", html_text, re.S).group(0)
    assert ">M6<" in row and ">MR<" in row
    assert ">3<" in row
    for pid in ("L002", "L004", "L009"):
        assert pid in row


def test_the_gap_table_is_ordered_worst_first_as_given():
    """Row order is the data layer's order, not re-sorted by the renderer.

    Fails if the renderer sorts the rows itself, which would break the "first row is the
    batch re-export to run" contract that completeness_gaps establishes.
    """
    gaps = [{"timepoint": "M12", "modality": "MR", "n_patients": 5, "patients": ["A"]},
            {"timepoint": "baseline", "modality": "CT", "n_patients": 2, "patients": ["B"]}]
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, gaps)
    pairs = re.findall(r"<td class='gtp'>([^<]+)</td><td class='gmod'>([^<]+)</td>", html_text)
    assert pairs == [("M12", "MR"), ("baseline", "CT")]


def test_a_gap_with_more_than_eight_patients_is_truncated_with_a_count():
    """Beyond 8 names, show 8 and '+N more' — a 98-name cell is not a table cell.

    The full list stays in the CSV export, so nothing is lost. Fails if the cut-off moves
    or the remainder marker disappears, which would either wreck the layout or hide the
    true size of the gap.
    """
    patients = [f"L{i:03d}" for i in range(1, 13)]
    gaps = [{"timepoint": "M6", "modality": "MR", "n_patients": 12, "patients": patients}]
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, gaps)
    who = re.search(r"<td class='gwho'[^>]*>(.*?)</td>", html_text, re.S).group(1)
    for pid in patients[:8]:
        assert pid in who
    for pid in patients[8:]:
        assert pid not in who
    assert "+4 more" in who


def test_a_gap_with_exactly_eight_patients_is_not_truncated():
    """Eight names fit; no '+0 more' marker may appear.

    Fails on an off-by-one in the cut-off.
    """
    patients = [f"L{i:03d}" for i in range(1, 9)]
    gaps = [{"timepoint": "M6", "modality": "MR", "n_patients": 8, "patients": patients}]
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, gaps)
    who = re.search(r"<td class='gwho'[^>]*>(.*?)</td>", html_text, re.S).group(1)
    assert all(pid in who for pid in patients)
    assert "more" not in who


def test_a_cohort_with_no_gap_says_so_instead_of_rendering_an_empty_table():
    """No missing object must read as a result, not as a table with no rows.

    Fails if an empty gap table is rendered, which looks like a broken page rather than a
    clean cohort.
    """
    html_text = completeness_section_html(GRID_TWO, _kpis(n_patients=2, n_complete=2),
                                          TINY_PROTOCOL, [])
    assert "No gap: every expected object is present." in html_text
    assert "id='comp-gaps'" not in html_text
    assert "<tr class='grow'" not in html_text


def test_the_per_patient_grid_is_collapsed_by_default():
    """The grid lives in a native <details>, closed, so the page opens on the headline.

    Native <details> keeps keyboard and screen-reader behaviour for free. Fails if the
    element gains an ``open`` attribute (the grid would be the headline again) or is
    replaced by a JS toggle.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    details = re.search(r"<details[^>]*>", html_text).group(0)
    assert " open" not in details, "the per-patient grid must start collapsed"
    assert "id='comp-grid'" in html_text.split(details, 1)[1], "the grid is not inside <details>"
    assert "</details>" in html_text


def test_the_drill_down_summary_states_how_many_patients_it_holds():
    """The summary must say what is behind it, with a correct plural.

    Fails if the summary is a bare "Details", which gives a reader no reason to open it.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    assert "Per-patient detail (2 patients)" in html_text
    one = [GRID_TWO[0]]
    solo = completeness_section_html(one, _kpis(n_patients=1), TINY_PROTOCOL, GAPS_TWO)
    assert "Per-patient detail (1 patient)" in solo
    assert "(1 patients)" not in solo


def test_the_grid_toolbar_and_interactions_survive_the_demotion():
    """Filter, only-incomplete toggle, CSV export and sticky header stay inside the details.

    Fails if demoting the grid quietly dropped its interactions; the drill-down is where a
    98-patient cohort actually gets worked through.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    body = html_text.split("<details", 1)[1]
    for control in ("id='comp-filter'", "id='comp-only-missing'",
                    "id='comp-export'", "id='comp-grid'"):
        assert control in body, f"{control} did not survive the move into the drill-down"


# --------------------------------------------------------------------------- #
# 7. Structure, scale and interactions
# --------------------------------------------------------------------------- #

def test_grid_has_one_column_per_protocol_timepoint():
    """Columns are timepoints, not timepoint x modality — the 24-label problem is gone.

    Fails if the renderer goes back to one column per (timepoint, modality) pair.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    headers = re.findall(r"<th class='ctp'[^>]*>([^<]+)</th>", html_text)
    assert headers == TINY_PROTOCOL.timepoints


def test_row_header_carries_the_patient_and_its_missing_counts():
    """Row header = patient id + n_missing/n_expected, so a row is actionable on its own.

    Fails if the counts move out of the row header (the reader would have to hover).
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    assert "L004" in html_text
    assert "2 / 4 missing" in html_text


def test_section_exposes_filter_toggle_and_csv_export_controls():
    """The grid gets the RT table's interactions: filter, only-incomplete toggle, export.

    Fails if any of the three controls the brief requires is dropped.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    assert "id='comp-filter'" in html_text
    assert "id='comp-only-missing'" in html_text
    assert "id='comp-export'" in html_text
    assert "id='comp-grid'" in html_text


def test_rows_carry_the_data_attributes_the_toggle_and_export_need():
    """The toggle keys off data-missing; the CSV export off per-chip data attributes.

    Fails if the JS hooks are renamed on one side only.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL, GAPS_TWO)
    assert "data-missing='2'" in html_text
    assert "data-tp='M3'" in html_text
    assert "data-mod='MR'" in html_text
    assert "data-state='MISSING'" in html_text


def _run_js(source, tmp_path):
    """Execute a self-contained JS snippet under node and return its JSON stdout."""
    script = tmp_path / "snippet.js"
    script.write_text(source, encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), str(script)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable for JS execution")
def test_csv_export_keeps_a_patient_that_has_no_chips(tmp_path):
    """A patient the protocol expects nothing of must still get a CSV row, never vanish.

    The export walks the chips, and a "nothing expected" patient has none — so it silently
    dropped them from the file. A patient absent from an export is a data-integrity defect:
    a reader reconciling the CSV against the cohort would conclude the patient does not
    exist. Fails if the chipless branch is removed or renamed; runs the real export logic,
    so it cannot be satisfied by a comment.
    """
    rows = _run_js(_csv_rows_js() + """
      console.log(JSON.stringify(compCsvRows([
        {patient:'L004', chips:[{timepoint:'M6', modality:'MR', state:'MISSING'}]},
        {patient:'L098', chips:[]}
      ])));
    """, tmp_path)
    assert rows[0] == ["patient", "timepoint", "modality", "state"]
    assert ["L004", "M6", "MR", "MISSING"] in rows
    assert ["L098", "", "", "nothing expected"] in rows
    assert len(rows) == 3, f"expected header + two patients, got {rows}"


def test_the_export_handler_routes_through_the_shared_row_builder():
    """The DOM walk must call compCsvRows, not rebuild the rows itself.

    Fails if the click handler ever grows its own inline row loop again, which is how the
    chipless patient was dropped in the first place.
    """
    script = completeness_script()
    assert "compCsvRows(" in script
    assert script.count("function compCsvRows") == 1


def _css_rules(css):
    """(selector, declarations) for every rule in a flat stylesheet."""
    out = []
    for block in css.split("}"):
        if "{" not in block:
            continue
        sel, body = block.rsplit("{", 1)
        out.append((sel.strip().splitlines()[-1].strip(), body))
    return out


def test_no_legend_rule_outranks_the_swatch_variants():
    """A compound selector such as `.clegend .sw` (0,2,0) silently beats `.sw-blank` (0,1,0).

    That is exactly what shipped: the blank swatch rendered solid instead of dashed and all
    four state swatches took the same grey border, because a redundant descendant rule
    out-specified every variant. Fails if any border-declaring rule that matches a legend
    swatch carries more class selectors than the `.sw-*` variants it must not override.
    """
    variants = [s for s, _b in _css_rules(completeness_styles()) if s.startswith(".sw-")]
    assert variants, "no swatch variant rules found"
    max_variant = max(s.count(".") for s in variants)
    for sel, body in _css_rules(completeness_styles()):
        if "border" not in body:
            continue
        for part in (p.strip() for p in sel.split(",")):
            if ".sw" not in part:
                continue
            assert part.count(".") <= max_variant, (
                f"{part!r} out-specifies the .sw-* variants and silently overrides them"
            )


def test_grid_table_reclaims_overflow_so_the_sticky_header_survives():
    """The base .grid rounds its corners with overflow:hidden, which kills position:sticky.

    Caught in the browser at 98 patients: the column labels scrolled away because an
    ancestor with overflow:hidden clips a sticky child. Fails if the .cgrid override is
    dropped and the header stops following the reader down the cohort.
    """
    css = completeness_styles()
    assert re.search(r"\.cgrid\{[^}]*overflow:visible", css), (
        "the completeness table does not hand its overflow back to the scroll wrapper"
    )
    assert re.search(r"\.cgrid thead th\{[^}]*position:sticky", css)
    assert re.search(r"\.cgrid-scroll\{[^}]*overflow:auto", css)


def test_empty_grid_renders_a_note_instead_of_an_empty_table():
    """No data must produce an explanatory note, not a headerless table or a crash.

    Fails if the renderer assumes ``grid[0]`` exists.
    """
    html_text = completeness_section_html([], _kpis(), TINY_PROTOCOL, [])
    assert "class='note'" in html_text
    assert "<tr class='crow'" not in html_text


# --------------------------------------------------------------------------- #
# 8. Standalone page (the `completeness` CLI command)
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def standalone_page(longitudinal, tmp_path_factory):
    """Render the standalone completeness page once; return (html_text, path)."""
    _root, idx = longitudinal
    state_df, hover_df, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    out = tmp_path_factory.mktemp("comp_map") / "map.html"
    result = render_completeness_map(state_df, hover_df, long_df, str(out), DEFAULT_PROTOCOL)
    return Path(result).read_text(encoding="utf-8"), Path(result)


def test_standalone_page_renders_the_same_grid_markup(standalone_page, longitudinal):
    """The standalone page must embed the shared section, not a second implementation.

    Fails the moment the standalone output diverges from completeness_section_html.
    """
    html_text, _ = standalone_page
    _root, idx = longitudinal
    _s, _h, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    section = completeness_section_html(completeness_grid(long_df),
                                        completeness_kpis(long_df), DEFAULT_PROTOCOL,
                                        completeness_gaps(long_df))
    assert section in html_text


def test_standalone_page_references_no_external_script_or_stylesheet(standalone_page):
    """It must still open by double-click on an air-gapped clinical workstation.

    Fails if any <script src=> or <link href=> creeps back in (e.g. a CDN chart library).
    """
    html_text, _ = standalone_page
    assert "<script src=" not in html_text
    assert "<link href=" not in html_text
    assert "<link rel=" not in html_text
    assert "https://" not in html_text


def test_standalone_page_no_longer_ships_plotly(standalone_page):
    """Dropping the 4.9 MB Plotly bundle is the point: the page must be small.

    Fails if a chart library is embedded again; 400 KB leaves ample room for a 98-patient
    grid while excluding any bundled library.
    """
    html_text, path = standalone_page
    assert "plotly" not in html_text.lower()
    assert path.stat().st_size < 400_000, f"page grew to {path.stat().st_size} bytes"


def test_standalone_page_keeps_the_research_use_only_banner(standalone_page):
    """The RUO regulatory line must survive the rewrite, in the header and the footer.

    Fails if the new page drops the disclaimer that makes it shippable.
    """
    html_text, _ = standalone_page
    assert html_text.count("Research Use Only") >= 2


def test_page_stylesheet_places_the_completeness_rules_after_the_base_rules(standalone_page):
    """Two overrides tie on specificity with the base rules, so *source order* decides them.

    `.cgrid{overflow:visible}` has to beat `.grid{overflow:hidden}` (or the sticky header dies)
    and `.sw-blank{border-style:dashed}` has to beat `.sw{border:...}` (or the blank swatch is
    solid). Both are 0,1,0 against 0,1,0. Swapping the two stylesheet calls in the page
    template reinstates both bugs while every isolated-CSS test still passes, so the order is
    asserted on the rendered page itself.
    """
    html_text, _ = standalone_page
    css = html_text.split("<style>", 1)[1].split("</style>", 1)[0]
    assert css.index(".cgrid{") > css.index(".grid{"), "completeness CSS emitted before the base CSS"
    assert css.index(".sw-blank{") > css.index(".sw{"), "swatch variants emitted before the base .sw"


def test_standalone_page_has_a_single_stylesheet_and_a_sticky_header(standalone_page):
    """Scale: one <style> for the whole page, and a header row that stays visible.

    Fails if per-patient inline <style> blocks are emitted, or the sticky header is lost
    (at 98 patients the column labels would scroll away).
    """
    html_text, _ = standalone_page
    assert html_text.count("<style") == 1
    assert "position:sticky" in html_text
    assert "<tr class='crow'" in html_text
