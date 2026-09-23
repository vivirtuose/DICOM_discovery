"""Tests for the observed-vs-expected completeness model (index/date-driven) and renderer."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import (  # noqa: E402
    DEFAULT_PROTOCOL,
    CellState,
    Protocol,
    build_completeness,
    completeness_gaps,
    completeness_grid,
    completeness_kpis,
    load_protocol,
    patient_completeness,
    render_completeness_map,
)


def _table(rows):
    """Build a minimal index-like table (one row per series)."""
    cols = ["patient_id", "study_uid", "study_date", "series_uid", "modality"]
    return pd.DataFrame(rows, columns=cols)


def _cell(state_df, patient, timepoint, modality):
    return state_df.loc[patient, f"{timepoint} │ {modality}"]


def test_cell_states_cover_all_four_cases():
    # Patient A: one baseline study (date 0) with CT (expected) and PT (extra); MR missing.
    table = _table([
        ["A", "S1", "20200106", "cser", "CT"],
        ["A", "S1", "20200106", "pser", "PT"],
    ])
    state_df, _hover, _long = build_completeness(table, DEFAULT_PROTOCOL)
    assert _cell(state_df, "A", "baseline", "CT") == int(CellState.PRESENT)
    assert _cell(state_df, "A", "baseline", "MR") == int(CellState.MISSING)
    assert _cell(state_df, "A", "baseline", "PT") == int(CellState.EXTRA)
    assert _cell(state_df, "A", "M3", "MR") == int(CellState.MISSING)
    assert _cell(state_df, "A", "M3", "CT") == int(CellState.NA)


def test_unmapped_patient_never_shows_missing():
    """A patient with data but no parseable StudyDate must be UNMAPPED, never MISSING."""
    table = _table([
        ["B", "S1", "", "cser", "CT"],
        ["B", "S1", "", "mser", "MR"],
    ])
    state_df, _hover, long_df = build_completeness(table, DEFAULT_PROTOCOL)
    row = state_df.loc["B"]
    assert (row == int(CellState.UNMAPPED)).all()
    assert (row == int(CellState.MISSING)).sum() == 0
    # UNMAPPED patients are excluded from the completeness %.
    assert patient_completeness(long_df).empty or "B" not in set(patient_completeness(long_df)["patient"])


def test_load_protocol_matches_default():
    proto = load_protocol(str(_ROOT / "protocol.brain_rt_followup.yaml"))
    assert isinstance(proto, Protocol)
    assert proto.timepoints == DEFAULT_PROTOCOL.timepoints
    assert proto.required == DEFAULT_PROTOCOL.required
    assert proto.offsets == DEFAULT_PROTOCOL.offsets


def test_longitudinal_completeness(longitudinal):
    _root, idx = longitudinal
    state_df, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    summary = patient_completeness(long_df).set_index("patient")

    assert summary.loc["L001", "pct_complete"] == 100.0
    assert _cell(state_df, "L002", "M6", "MR") == int(CellState.MISSING)
    assert summary.loc["L002", "n_missing"] == 1
    assert _cell(state_df, "L003", "baseline", "RTDOSE") == int(CellState.MISSING)
    for tp in ("M3", "M6", "M12"):
        assert _cell(state_df, "L004", tp, "MR") == int(CellState.MISSING)
    assert (state_df.loc["L005"] == int(CellState.EXTRA)).any()


def _long(rows):
    """Build a long_df-shaped frame directly, preserving row order (it encodes protocol order)."""
    cols = ["patient", "timepoint", "modality", "expected", "observed", "state"]
    return pd.DataFrame(rows, columns=cols)


def test_completeness_grid_orders_patients_worst_first(longitudinal):
    """completeness_grid must rank by n_missing descending; a smaller n_missing anywhere
    out of order would mean the sort key (or its tie-break) is wrong."""
    _root, idx = longitudinal
    _state_df, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    grid = completeness_grid(long_df)
    n_missing = [p["n_missing"] for p in grid]
    assert n_missing == sorted(n_missing, reverse=True)
    # tie-break is patient ascending: within any run of equal n_missing, patients climb.
    for a, b in zip(grid, grid[1:]):
        if a["n_missing"] == b["n_missing"]:
            assert a["patient"] <= b["patient"]


def test_completeness_grid_matches_patient_completeness(longitudinal):
    """Per-patient aggregate fields must come from patient_completeness, not be recomputed —
    if completeness_grid ever diverged (e.g. a different rounding), this would catch it."""
    _root, idx = longitudinal
    _state_df, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    grid = completeness_grid(long_df)
    summary = patient_completeness(long_df).set_index("patient")
    by_patient = {p["patient"]: p for p in grid}
    assert by_patient["L002"]["n_missing"] == 1
    for patient, row in summary.iterrows():
        g = by_patient[patient]
        assert g["n_expected"] == int(row["n_expected"])
        assert g["n_present"] == int(row["n_present"])
        assert g["n_missing"] == int(row["n_missing"])
        assert g["pct_complete"] == float(row["pct_complete"])


def test_completeness_grid_cell_state_is_worst_of_its_items():
    """A cell's state is the worst item state present, over the full five-tier chain
    MISSING > UNMAPPED > EXTRA > PRESENT > NA. Each timepoint below isolates one adjacent
    pair in that chain, so swapping any two tiers in _CELL_RANK breaks a specific assertion
    rather than passing by accident (e.g. swapping EXTRA/UNMAPPED flips M12 to 'EXTRA')."""
    long_df = _long([
        ["A", "baseline", "CT", True, True, "PRESENT"],
        ["A", "baseline", "MR", True, False, "MISSING"],
        ["A", "M3", "CT", False, True, "EXTRA"],
        ["A", "M3", "MR", True, True, "PRESENT"],
        ["A", "M6", "CT", True, False, "MISSING"],
        ["A", "M6", "MR", True, False, "UNMAPPED"],
        ["A", "M12", "CT", True, False, "UNMAPPED"],
        ["A", "M12", "MR", False, True, "EXTRA"],
        ["A", "M12", "RTDOSE", True, True, "PRESENT"],
    ])
    grid = completeness_grid(long_df)
    cells = {c["timepoint"]: c for c in grid[0]["cells"]}
    assert cells["baseline"]["state"] == "MISSING"   # MISSING > PRESENT
    assert cells["M3"]["state"] == "EXTRA"            # EXTRA > PRESENT
    assert cells["M6"]["state"] == "MISSING"          # MISSING > UNMAPPED
    assert cells["M12"]["state"] == "UNMAPPED"        # UNMAPPED > EXTRA > PRESENT


def test_completeness_grid_excludes_na_items_but_keeps_the_cell():
    """NA rows ('not expected and not observed') must produce no item — a renderer drawing
    every item would show clutter where nothing was ever asked for."""
    long_df = _long([
        ["A", "baseline", "CT", True, True, "PRESENT"],
        ["A", "baseline", "PT", False, False, "NA"],
    ])
    grid = completeness_grid(long_df)
    cell = grid[0]["cells"][0]
    assert len(cell["items"]) == 1
    assert cell["items"][0]["modality"] == "CT"


def test_completeness_grid_all_na_timepoint_yields_empty_na_cell():
    """A timepoint whose items are all NA still gets a cell (state NA, items empty) — dropping
    the cell entirely would break 'one entry per protocol timepoint'."""
    long_df = _long([
        ["A", "baseline", "CT", True, True, "PRESENT"],
        ["A", "M3", "CT", False, False, "NA"],
        ["A", "M3", "MR", False, False, "NA"],
    ])
    grid = completeness_grid(long_df)
    m3 = [c for c in grid[0]["cells"] if c["timepoint"] == "M3"][0]
    assert m3["items"] == []
    assert m3["state"] == "NA"


def test_completeness_grid_preserves_protocol_order_not_alphabetical():
    """Timepoint order must follow appearance order in long_df, not string sort — 'M12' sorts
    before 'M3' alphabetically, which would silently reorder a real protocol's timeline."""
    long_df = _long([
        ["A", "baseline", "CT", True, True, "PRESENT"],
        ["A", "M3", "CT", True, True, "PRESENT"],
        ["A", "M12", "CT", True, True, "PRESENT"],
    ])
    grid = completeness_grid(long_df)
    assert [c["timepoint"] for c in grid[0]["cells"]] == ["baseline", "M3", "M12"]


def test_completeness_grid_patient_with_only_na_cells_still_appears():
    """A patient absent from patient_completeness (nothing expected) must still surface in the
    grid — dropping them would hide a patient from the QC registry entirely."""
    long_df = _long([
        ["Z", "baseline", "CT", False, False, "NA"],
        ["Z", "M3", "CT", False, False, "NA"],
    ])
    grid = completeness_grid(long_df)
    assert len(grid) == 1
    row = grid[0]
    assert row["patient"] == "Z"
    assert row["n_expected"] == 0 and row["n_present"] == 0 and row["n_missing"] == 0
    assert all(c["items"] == [] and c["state"] == "NA" for c in row["cells"])


def test_completeness_grid_empty_long_df_returns_empty_list():
    assert completeness_grid(pd.DataFrame(
        columns=["patient", "timepoint", "modality", "expected", "observed", "state"])) == []


def test_completeness_kpis_basic_counts(longitudinal):
    """n_patients/n_complete/n_incomplete must partition the cohort exactly (no double count,
    none dropped) — a patient miscounted here would silently skew a cohort-level KPI."""
    _root, idx = longitudinal
    _state_df, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    kpis = completeness_kpis(long_df)
    grid = completeness_grid(long_df)
    assert kpis["n_patients"] == len(grid)
    assert kpis["n_complete"] + kpis["n_incomplete"] == kpis["n_patients"]
    assert kpis["n_complete"] == sum(1 for p in grid if p["n_missing"] == 0)


def test_completeness_kpis_worst_gap_is_the_most_common_missing_pair():
    """worst_gap must pick the (timepoint, modality) that is MISSING for the most patients,
    not merely the first MISSING cell encountered."""
    long_df = _long([
        ["A", "baseline", "MR", True, False, "MISSING"],
        ["B", "baseline", "MR", True, False, "MISSING"],
        ["C", "baseline", "CT", True, False, "MISSING"],
    ])
    kpis = completeness_kpis(long_df)
    assert kpis["worst_gap"] == {"timepoint": "baseline", "modality": "MR", "n_patients": 2}


def test_completeness_kpis_worst_gap_tie_breaks_by_protocol_order_then_modality():
    """Equal MISSING counts must resolve deterministically: earlier protocol timepoint wins,
    then modality name — otherwise the KPI would flap between equally-valid runs."""
    long_df = _long([
        ["A", "baseline", "CT", True, False, "MISSING"],
        ["B", "M3", "MR", True, False, "MISSING"],
    ])
    kpis = completeness_kpis(long_df)
    assert kpis["worst_gap"] == {"timepoint": "baseline", "modality": "CT", "n_patients": 1}


def test_completeness_kpis_n_unmapped_counts_unmapped_cells():
    """n_unmapped counts UNMAPPED *cells* across the cohort — used to flag patients whose
    dates couldn't be placed on the timeline, distinct from actual missing data."""
    long_df = _long([
        ["A", "baseline", "CT", True, False, "UNMAPPED"],
        ["A", "M3", "MR", True, False, "UNMAPPED"],
        ["B", "baseline", "CT", True, True, "PRESENT"],
    ])
    kpis = completeness_kpis(long_df)
    assert kpis["n_unmapped"] == 2


def test_completeness_kpis_no_missing_means_worst_gap_none_and_all_complete():
    """A cohort with nothing MISSING must report worst_gap: None and n_complete == n_patients —
    covers the 'good cohort' edge case explicitly, not just by absence of a crash."""
    long_df = _long([
        ["A", "baseline", "CT", True, True, "PRESENT"],
        ["B", "baseline", "CT", True, True, "PRESENT"],
    ])
    kpis = completeness_kpis(long_df)
    assert kpis["worst_gap"] is None
    assert kpis["n_complete"] == kpis["n_patients"] == 2


def test_completeness_kpis_empty_long_df_returns_zeros_without_raising():
    empty = pd.DataFrame(columns=["patient", "timepoint", "modality", "expected", "observed", "state"])
    kpis = completeness_kpis(empty)
    assert kpis == {"n_patients": 0, "n_complete": 0, "n_incomplete": 0,
                    "pct_complete_mean": 0.0, "worst_gap": None, "n_unmapped": 0}


def test_render_map_is_self_contained(longitudinal, tmp_path):
    """The standalone page must open air-gapped and carry the four actionable states.

    v0.12 replaced the embedded Plotly heatmap with a plain HTML grid, so the old
    "Plotly is embedded, page > 1 MB" assertions were inverted rather than dropped: the
    guarantee is still 'nothing is fetched at view time', now proven by the absence of any
    external reference *and* of any bundled library. Would fail if a CDN reference, a
    chart bundle, or one of the state labels came back.
    """
    _root, idx = longitudinal
    state_df, hover_df, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    out = render_completeness_map(state_df, hover_df, long_df,
                                  str(tmp_path / "map.html"), DEFAULT_PROTOCOL)
    page = Path(out).read_text(encoding="utf-8")
    assert "plotly" not in page.lower()
    assert "<script src=" not in page and "<link href=" not in page
    assert "https://" not in page
    assert "MISSING" in page and "PRESENT" in page and "UNMAPPED" in page


# --------------------------------------------------------------------------- #
# completeness_gaps — the cohort-level headline (Task 2b)
# --------------------------------------------------------------------------- #

def test_completeness_gaps_orders_by_patient_count_descending():
    """The gap that affects most patients must lead: it is the one batch re-export to run.

    Would fail if the list kept insertion order, or sorted by timepoint, burying the gap
    that a single action would close for the most patients.
    """
    long_df = _long([
        ["A", "baseline", "CT", True, True, "PRESENT"],
        ["A", "M3", "MR", True, False, "MISSING"],
        ["A", "M6", "MR", True, False, "MISSING"],
        ["B", "baseline", "CT", True, True, "PRESENT"],
        ["B", "M6", "MR", True, False, "MISSING"],
        ["C", "baseline", "CT", True, True, "PRESENT"],
        ["C", "M6", "MR", True, False, "MISSING"],
    ])
    gaps = completeness_gaps(long_df)
    assert [(g["timepoint"], g["modality"], g["n_patients"]) for g in gaps] == [
        ("M6", "MR", 3), ("M3", "MR", 1)]


def test_completeness_gaps_tie_breaks_by_protocol_order_then_modality():
    """Ties resolve by protocol order of the timepoint, then modality name — deterministic.

    Same convention as completeness_kpis' worst_gap. Would fail if ties fell back to
    dictionary order (M12 would precede M3) or to first-seen order.
    """
    long_df = _long([
        ["A", "baseline", "CT", True, False, "MISSING"],
        ["A", "baseline", "MR", True, False, "MISSING"],
        ["A", "M3", "MR", True, False, "MISSING"],
        ["A", "M12", "MR", True, False, "MISSING"],
    ])
    gaps = completeness_gaps(long_df)
    # All four gaps affect exactly one patient, so only the tie-break orders them.
    assert all(g["n_patients"] == 1 for g in gaps)
    assert [(g["timepoint"], g["modality"]) for g in gaps] == [
        ("baseline", "CT"), ("baseline", "MR"), ("M3", "MR"), ("M12", "MR")]


def test_completeness_gaps_lists_every_affected_patient_sorted():
    """The patient list must be complete and sorted — no truncation at the data layer.

    Truncation is a rendering decision; a data layer that dropped names would make the CSV
    export (rebuilt from the same source) silently incomplete.
    """
    rows = [[f"P{i:02d}", "M6", "MR", True, False, "MISSING"] for i in range(12, -1, -1)]
    gaps = completeness_gaps(_long(rows))
    assert len(gaps) == 1
    assert gaps[0]["n_patients"] == 13
    assert gaps[0]["patients"] == sorted(gaps[0]["patients"])
    assert len(gaps[0]["patients"]) == 13


def test_completeness_gaps_counts_patients_not_cells():
    """One patient missing the same pair twice must still count once.

    Cannot happen through build_completeness today, but the field is named n_patients and
    must mean that; would fail if the implementation counted MISSING rows.
    """
    long_df = _long([
        ["A", "M6", "MR", True, False, "MISSING"],
        ["A", "M6", "MR", True, False, "MISSING"],
    ])
    gaps = completeness_gaps(long_df)
    assert gaps[0]["n_patients"] == 1
    assert gaps[0]["patients"] == ["A"]


def test_completeness_gaps_ignores_states_that_are_not_missing():
    """Only MISSING is a gap. UNMAPPED especially must never be reported as one.

    Would fail if the filter widened to "not present", which would tell a clinician to chase
    scans that exist but fell outside a protocol window.
    """
    long_df = _long([
        ["A", "baseline", "CT", True, True, "PRESENT"],
        ["A", "baseline", "PT", False, True, "EXTRA"],
        ["A", "M3", "MR", True, False, "UNMAPPED"],
        ["A", "M6", "MR", False, False, "NA"],
    ])
    assert completeness_gaps(long_df) == []


def test_completeness_gaps_empty_long_df_returns_empty_list_without_raising():
    empty = pd.DataFrame(columns=["patient", "timepoint", "modality",
                                  "expected", "observed", "state"])
    assert completeness_gaps(empty) == []
    assert completeness_gaps(None) == []


def test_completeness_gaps_first_entry_agrees_with_kpis_worst_gap(longitudinal):
    """gaps[0] and kpis['worst_gap'] must name the same pair and the same count.

    They are two views of one fact; if they could drift, the KPI line and the gap table on
    the same page would contradict each other. Would fail if either sort key or tie-break
    were changed on one side only.
    """
    _root, idx = longitudinal
    _state, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    gaps, kpis = completeness_gaps(long_df), completeness_kpis(long_df)
    assert gaps, "the longitudinal fixture is expected to have at least one gap"
    assert kpis["worst_gap"] == {"timepoint": gaps[0]["timepoint"],
                                 "modality": gaps[0]["modality"],
                                 "n_patients": gaps[0]["n_patients"]}


def test_completeness_gaps_agree_with_the_grid_on_the_real_cohort(longitudinal):
    """Every MISSING item in the grid must appear in exactly one gap entry, and vice versa.

    The gap table is an aggregation of the same data the grid shows; this pins the two
    together so a reader cannot find a patient in one and not the other.
    """
    _root, idx = longitudinal
    _state, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    from_grid = {(c["timepoint"], it["modality"], p["patient"])
                 for p in completeness_grid(long_df) for c in p["cells"]
                 for it in c["items"] if it["state"] == "MISSING"}
    from_gaps = {(g["timepoint"], g["modality"], pid)
                 for g in completeness_gaps(long_df) for pid in g["patients"]}
    assert from_grid == from_gaps


def test_worst_gap_and_the_gap_table_agree_on_a_duplicated_missing_row():
    """The KPI line and the gap table must never print two counts for the same gap.

    ``completeness_gaps`` deduplicates patients through a set; ``worst_gap`` used to
    increment once per MISSING *item*. On a long_df carrying the same (patient, timepoint,
    modality) twice the page said "most-missed in 2 patients" above a table whose own column
    said 1 — and a QC tool that contradicts itself on one number is not trusted on any of
    them. Would fail again the moment the two stop sharing a single definition of a gap.
    """
    long_df = _long([
        ["A", "M6", "MR", True, False, "MISSING"],
        ["A", "M6", "MR", True, False, "MISSING"],
    ])
    gaps, kpis = completeness_gaps(long_df), completeness_kpis(long_df)
    assert gaps[0]["n_patients"] == 1
    assert kpis["worst_gap"]["n_patients"] == gaps[0]["n_patients"]
    assert kpis["worst_gap"] == {"timepoint": gaps[0]["timepoint"],
                                 "modality": gaps[0]["modality"],
                                 "n_patients": gaps[0]["n_patients"]}
