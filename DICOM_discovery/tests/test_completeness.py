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


def test_render_map_is_self_contained(longitudinal, tmp_path):
    _root, idx = longitudinal
    state_df, hover_df, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    out = render_completeness_map(state_df, hover_df, long_df,
                                  str(tmp_path / "map.html"), DEFAULT_PROTOCOL)
    page = Path(out).read_text(encoding="utf-8")
    assert "Plotly" in page
    assert len(page) > 1_000_000
    assert 'src="https://cdn.plot.ly' not in page
    assert "MISSING" in page and "PRESENT" in page and "UNMAPPED" in page
