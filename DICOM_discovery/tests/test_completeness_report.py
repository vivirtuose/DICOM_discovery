"""Tests for the completeness *renderer* (report_completeness.py).

The data layer (``completeness_grid`` / ``completeness_kpis``) is covered by
``test_completeness.py``; here we only assert what a clinician ends up looking at:
no grey "not expected" boxes, worst patients on top, every chip legible without
colour, and a standalone page that still opens on an air-gapped workstation.

Most tests feed hand-built grid/kpi dicts (the Task-1 contract) rather than a real
cohort, so a rendering regression is not masked by a change in the synthetic data.
"""
from __future__ import annotations

import re
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
    completeness_grid,
    completeness_kpis,
    render_completeness_map,
)
from DICOM_discovery.report_completeness import (  # noqa: E402
    completeness_section_html,
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
    html_text = completeness_section_html(grid, _kpis(n_patients=1), TINY_PROTOCOL)

    assert len(_chips(html_text)) == 2, "an empty cell rendered a chip"
    assert "chip-NA" not in html_text
    # The empty cell is still a cell (column alignment survives) but carries no ink.
    assert "<td class='ccell empty'></td>" in html_text


def test_no_not_expected_legend_entry_survives():
    """The legend must not reintroduce a grey 'not expected' swatch.

    Fails if someone re-adds an NA legend row: the whole point is that 'not expected'
    is communicated by blankness, explained once in prose, not by a fifth colour.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL)
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
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL)
    order = re.findall(r"<tr class='crow'[^>]*data-pid='([^']+)'", html_text)
    assert order == ["L004", "L001"]


def test_worst_first_order_survives_the_real_cohort(longitudinal):
    """End-to-end: the rendered row order equals completeness_grid's order on real data.

    Fails if the renderer (or the section's DOM assembly) reverses or regroups rows.
    """
    _root, idx = longitudinal
    _state, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    grid = completeness_grid(long_df)
    html_text = completeness_section_html(grid, completeness_kpis(long_df), DEFAULT_PROTOCOL)
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
    html_text = completeness_section_html(grid, _kpis(n_patients=1), TINY_PROTOCOL)
    chips = _chips(html_text)
    assert len(chips) == 1
    chip = chips[0]
    assert f"chip-{state}" in chip, "state missing from the CSS class"
    assert f"aria-label='M12 MR {word}'" in chip, "state missing from the aria-label"
    assert f">{word}</span>" in chip, "state word missing from the chip text"
    assert ">MR<" in chip, "modality label missing from the chip"


def test_every_chip_in_the_real_cohort_has_an_aria_label(longitudinal):
    """Not one chip may slip through without an accessible state label.

    Fails if a state branch of the chip renderer forgets its aria-label.
    """
    _root, idx = longitudinal
    _state, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    html_text = completeness_section_html(completeness_grid(long_df),
                                          completeness_kpis(long_df), DEFAULT_PROTOCOL)
    chips = _chips(html_text)
    assert chips, "no chips rendered for the real cohort"
    assert all("aria-label='" in c for c in chips)


# --------------------------------------------------------------------------- #
# 4. The KPI line
# --------------------------------------------------------------------------- #

def test_kpi_line_names_the_most_missed_timepoint_and_modality():
    """The most-missed gap must be named in plain English, timepoint and modality both.

    This is the line that triggers a batch re-export; fails if it degrades to a bare count.
    """
    kpis = _kpis(n_patients=9, n_complete=4, n_incomplete=5,
                 worst_gap={"timepoint": "M12", "modality": "MR", "n_patients": 3})
    html_text = completeness_section_html(GRID_TWO, kpis, TINY_PROTOCOL)
    assert "M12 - MR" in html_text
    assert "most-missed in 3 patients" in html_text
    assert "Most-missed: M12 - MR (3 patients)" in html_text


def test_kpi_line_uses_singular_wording_for_a_single_patient():
    """'1 patient', not '1 patients' — this text is read by clinicians, not parsers.

    Fails if the plural is hard-coded.
    """
    kpis = _kpis(n_patients=3, n_complete=2,
                 worst_gap={"timepoint": "M3", "modality": "CT", "n_patients": 1})
    html_text = completeness_section_html(GRID_TWO, kpis, TINY_PROTOCOL)
    assert "most-missed in 1 patient" in html_text
    assert "1 patients" not in html_text


def test_kpi_line_omits_the_most_missed_segment_when_nothing_is_missing():
    """A clean cohort must not show an empty 'most-missed' slot.

    Fails if the segment is rendered unconditionally with a None worst_gap.
    """
    kpis = _kpis(n_patients=2, n_complete=2, worst_gap=None)
    html_text = completeness_section_html(GRID_TWO, kpis, TINY_PROTOCOL)
    assert "most-missed" not in html_text
    assert "None" not in html_text


def test_kpi_line_omits_the_unmapped_note_when_nothing_is_unmapped():
    """No unmapped data means no data-quality note at all.

    Fails if a '0 not placed on the timeline' segment is rendered when n_unmapped == 0.
    """
    html_text = completeness_section_html(GRID_TWO, _kpis(n_patients=2, n_unmapped=0),
                                          TINY_PROTOCOL)
    assert "not placed on the timeline" not in html_text


def test_kpi_line_shows_unmapped_as_a_data_quality_note_not_a_patient_failure():
    """Unmapped studies are a protocol-window problem; the wording must say so.

    Fails if the note is phrased as missing patient data (which would send a clinician
    hunting for scans that exist).
    """
    html_text = completeness_section_html(GRID_TWO, _kpis(n_patients=2, n_unmapped=7),
                                          TINY_PROTOCOL)
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
    html_text = completeness_section_html(grid, kpis, TINY_PROTOCOL)
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
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL)
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
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL)
    assert "CT, MR" in html_text


def test_protocol_line_names_the_default_protocol_timepoints():
    """Same guarantee against the real DEFAULT_PROTOCOL (four timepoints)."""
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, DEFAULT_PROTOCOL)
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
                                          TINY_PROTOCOL)
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
                                          TINY_PROTOCOL)
    assert "1 / 2" in html_text
    assert "2 / 2" not in html_text
    assert "nothing expected" in html_text
    assert "1 patient has nothing expected by this protocol" in html_text


# --------------------------------------------------------------------------- #
# 7. Structure, scale and interactions
# --------------------------------------------------------------------------- #

def test_grid_has_one_column_per_protocol_timepoint():
    """Columns are timepoints, not timepoint x modality — the 24-label problem is gone.

    Fails if the renderer goes back to one column per (timepoint, modality) pair.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL)
    headers = re.findall(r"<th class='ctp'[^>]*>([^<]+)</th>", html_text)
    assert headers == TINY_PROTOCOL.timepoints


def test_row_header_carries_the_patient_and_its_missing_counts():
    """Row header = patient id + n_missing/n_expected, so a row is actionable on its own.

    Fails if the counts move out of the row header (the reader would have to hover).
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL)
    assert "L004" in html_text
    assert "2 / 4 missing" in html_text


def test_section_exposes_filter_toggle_and_csv_export_controls():
    """The grid gets the RT table's interactions: filter, only-incomplete toggle, export.

    Fails if any of the three controls the brief requires is dropped.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL)
    assert "id='comp-filter'" in html_text
    assert "id='comp-only-missing'" in html_text
    assert "id='comp-export'" in html_text
    assert "id='comp-grid'" in html_text


def test_rows_carry_the_data_attributes_the_toggle_and_export_need():
    """The toggle keys off data-missing; the CSV export off per-chip data attributes.

    Fails if the JS hooks are renamed on one side only.
    """
    html_text = completeness_section_html(GRID_TWO, KPIS_TWO, TINY_PROTOCOL)
    assert "data-missing='2'" in html_text
    assert "data-tp='M3'" in html_text
    assert "data-mod='MR'" in html_text
    assert "data-state='MISSING'" in html_text


def test_empty_grid_renders_a_note_instead_of_an_empty_table():
    """No data must produce an explanatory note, not a headerless table or a crash.

    Fails if the renderer assumes ``grid[0]`` exists.
    """
    html_text = completeness_section_html([], _kpis(), TINY_PROTOCOL)
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
                                        completeness_kpis(long_df), DEFAULT_PROTOCOL)
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


def test_standalone_page_has_a_single_stylesheet_and_a_sticky_header(standalone_page):
    """Scale: one <style> for the whole page, and a header row that stays visible.

    Fails if per-patient inline <style> blocks are emitted, or the sticky header is lost
    (at 98 patients the column labels would scroll away).
    """
    html_text, _ = standalone_page
    assert html_text.count("<style") == 1
    assert "position:sticky" in html_text
    assert "<tr class='crow'" in html_text
