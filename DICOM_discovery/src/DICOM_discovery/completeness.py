"""Longitudinal cohort completeness — observed vs. expected (v3, index-driven).

"Complete" only means something relative to a *protocol*: what each patient should have,
at each timepoint. This module consumes the canonical index (not the filesystem) and
derives the timepoint of each study from its **StudyDate**, relative to the patient's
baseline — because a timepoint label (baseline/M3/M6) is a protocol convention, not a
DICOM tag.

Cell states:
* ``PRESENT``  — expected and found;
* ``MISSING``  — expected but absent (the actionable one);
* ``EXTRA``    — found but not in the protocol;
* ``NA``       — not expected and not found;
* ``UNMAPPED`` — the patient has data but no study could be placed on the protocol
  timeline (e.g. missing/garbage StudyDate). Crucially this is **never** rendered as
  MISSING: a complete patient under odd dates must not look like missing data.
"""
from __future__ import annotations

import datetime
import enum
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

LOG = logging.getLogger("DICOM_discovery.completeness")


class CellState(enum.IntEnum):
    NA = 0
    PRESENT = 1
    MISSING = 2
    EXTRA = 3
    UNMAPPED = 4


@dataclass
class Protocol:
    """What the cohort must contain, per timepoint, plus the date windows that define them."""

    timepoints: List[str]
    required: Dict[str, List[str]]
    offsets: Dict[str, int]          # days from baseline for each timepoint
    tolerance_days: int = 60
    name: str = "protocol"

    def required_modalities(self) -> List[str]:
        seen: List[str] = []
        for tp in self.timepoints:
            for mod in self.required.get(tp, []):
                if mod not in seen:
                    seen.append(mod)
        return sorted(seen)


DEFAULT_PROTOCOL = Protocol(
    name="brain_rt_followup",
    timepoints=["baseline", "M3", "M6", "M12"],
    required={
        "baseline": ["CT", "MR", "RTSTRUCT", "RTPLAN", "RTDOSE"],
        "M3": ["MR"],
        "M6": ["MR"],
        "M12": ["MR"],
    },
    offsets={"baseline": 0, "M3": 90, "M6": 180, "M12": 360},
    tolerance_days=60,
)


def load_protocol(path: str) -> Protocol:
    """Load a protocol from YAML (keys: name, timepoints, required, offsets, tolerance_days)."""
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Protocol(
        name=data.get("name", "protocol"),
        timepoints=list(data["timepoints"]),
        required={k: list(v) for k, v in data["required"].items()},
        offsets={k: int(v) for k, v in data["offsets"].items()},
        tolerance_days=int(data.get("tolerance_days", 60)),
    )


def _parse_date(s) -> Optional[datetime.date]:
    s = str(s or "").strip()
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return datetime.datetime.strptime(s[:8], "%Y%m%d").date()
    except ValueError:
        return None


def assign_timepoints(table: pd.DataFrame, protocol: Protocol) -> pd.DataFrame:
    """Return the table with a derived ``timepoint`` column (label or 'UNMAPPED').

    Baseline = the patient's earliest parseable StudyDate; each study is placed on the
    nearest protocol offset within tolerance, else UNMAPPED.
    """
    df = table.copy()
    df["_date"] = df["study_date"].map(_parse_date)
    df["timepoint"] = "UNMAPPED"
    for _pid, grp in df.groupby("patient_id"):
        dates = grp["_date"].dropna()
        if dates.empty:
            continue
        baseline = dates.min()
        for idx, row in grp.iterrows():
            d = row["_date"]
            if d is None:
                continue
            offset = (d - baseline).days
            best, best_gap = None, None
            for label, off in protocol.offsets.items():
                gap = abs(offset - off)
                if gap <= protocol.tolerance_days and (best_gap is None or gap < best_gap):
                    best, best_gap = label, gap
            if best is not None:
                df.at[idx, "timepoint"] = best
    return df.drop(columns="_date")


def build_completeness(table: pd.DataFrame, protocol: Protocol = DEFAULT_PROTOCOL
                       ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Grade every (patient, timepoint, modality) cell against the protocol.

    Returns (state_df, hover_df, long_df). ``state_df`` is patients x "timepoint │ modality"
    of integer :class:`CellState`.
    """
    if table is None or table.empty:
        empty = pd.DataFrame()
        return empty, empty, empty

    tagged = assign_timepoints(table, protocol)
    patients = sorted(tagged["patient_id"].astype(str).unique(),
                      key=lambda p: (pd.to_numeric(pd.Series([p]).str.extract(r"(\d+)", expand=False)).iloc[0]
                                     if any(c.isdigit() for c in p) else float("inf"), p))
    observed_mods = {m for m in tagged["modality"].unique()}
    mods = sorted(set(protocol.required_modalities()) | observed_mods)

    # observed (patient, timepoint, modality) for studies that were placed on the timeline
    placed = tagged[tagged["timepoint"] != "UNMAPPED"]
    obs_index = set(zip(placed["patient_id"].astype(str), placed["timepoint"], placed["modality"]))
    # patients that have data but no placeable study at all
    mapped_any = set(placed["patient_id"].astype(str).unique())
    has_data = set(tagged["patient_id"].astype(str).unique())
    unmapped_patients = has_data - mapped_any

    columns = [f"{tp} │ {mod}" for tp in protocol.timepoints for mod in mods]
    state_rows, hover_rows, long_records = [], [], []

    for patient in patients:
        srow, hrow = [], []
        patient_unmapped = patient in unmapped_patients
        for tp in protocol.timepoints:
            req = set(protocol.required.get(tp, []))
            for mod in mods:
                is_expected = mod in req
                is_observed = (patient, tp, mod) in obs_index
                if patient_unmapped:
                    state = CellState.UNMAPPED
                elif is_expected and is_observed:
                    state = CellState.PRESENT
                elif is_expected and not is_observed:
                    state = CellState.MISSING
                elif not is_expected and is_observed:
                    state = CellState.EXTRA
                else:
                    state = CellState.NA
                srow.append(int(state))
                hrow.append(f"{patient} · {tp} · {mod}<br>expected: {'yes' if is_expected else 'no'} · "
                            f"observed: {'yes' if is_observed else 'no'}<br><b>{state.name}</b>")
                long_records.append({"patient": patient, "timepoint": tp, "modality": mod,
                                     "expected": is_expected, "observed": is_observed,
                                     "state": state.name})
        state_rows.append(srow)
        hover_rows.append(hrow)

    state_df = pd.DataFrame(state_rows, index=patients, columns=columns)
    hover_df = pd.DataFrame(hover_rows, index=patients, columns=columns)
    long_df = pd.DataFrame(long_records)
    if unmapped_patients:
        LOG.warning("%d patient(s) have data but no study mapped to the protocol timeline (UNMAPPED): %s",
                    len(unmapped_patients), ", ".join(sorted(unmapped_patients)))
    return state_df, hover_df, long_df


#: Worst-of ranking for a grid cell holding several items (e.g. two modalities at one
#: timepoint): MISSING is the actionable state and must win over everything, UNMAPPED must
#: still outrank EXTRA/PRESENT so a garbled-date patient reads as "can't tell", not "fine".
_CELL_RANK = {"NA": 0, "PRESENT": 1, "EXTRA": 2, "UNMAPPED": 3, "MISSING": 4}


def completeness_grid(long_df: pd.DataFrame) -> List[dict]:
    """Reshape long_df into one row per patient, ready for a renderer to walk with no pandas.

    Ordered worst-first (most MISSING on top) because a QC registry exists to be acted on:
    the patients needing attention belong where they're seen first, same idea as
    ``order_for_review`` in rt_integrity.py.
    """
    if long_df is None or long_df.empty:
        return []

    summary = patient_completeness(long_df).set_index("patient")
    grid: List[dict] = []
    for patient, pdf in long_df.groupby("patient", sort=False):
        # Row order within a patient's slice is long_df's own order, which build_completeness
        # writes in protocol.timepoints order — reuse it instead of sorting (M12 must not
        # come before M3).
        timepoints: List[str] = []
        for tp in pdf["timepoint"]:
            if tp not in timepoints:
                timepoints.append(tp)

        cells = []
        for tp in timepoints:
            items = [{"modality": row["modality"], "state": row["state"]}
                     for _, row in pdf[pdf["timepoint"] == tp].iterrows()
                     if row["state"] != "NA"]
            state = max((it["state"] for it in items), key=lambda s: _CELL_RANK[s], default="NA")
            cells.append({"timepoint": tp, "state": state, "items": items})

        if patient in summary.index:
            srow = summary.loc[patient]
            n_expected, n_present = int(srow["n_expected"]), int(srow["n_present"])
            n_missing, pct_complete = int(srow["n_missing"]), float(srow["pct_complete"])
        else:
            # Not in patient_completeness at all — either fully UNMAPPED (excluded there by
            # design, see module docstring) or nothing was ever expected of them. Either way
            # there is nothing to be missing, so they read as vacuously complete rather than
            # dropped from the registry.
            n_expected = n_present = n_missing = 0
            pct_complete = 100.0

        grid.append({"patient": patient, "n_expected": n_expected, "n_present": n_present,
                     "n_missing": n_missing, "pct_complete": pct_complete, "cells": cells})

    grid.sort(key=lambda p: (-p["n_missing"], p["patient"]))
    return grid


def completeness_gaps(long_df: pd.DataFrame) -> List[dict]:
    """Aggregate the cohort's MISSING cells into one entry per (timepoint, modality) pair.

    This is the shape an action takes. A per-patient grid answers "what is wrong with L004?";
    a QC round answers "which single re-export closes the most holes?", and that question is
    about the *pair*, not the patient. Ordered worst-first so the answer is the first row.

    Built on :func:`completeness_grid` rather than on ``long_df`` directly, for the same
    reason ``completeness_kpis`` is: three views of one cohort that derive from one another
    cannot contradict each other on the page.
    """
    grid = completeness_grid(long_df)
    if not grid:
        return []

    # Protocol order for the tie-break comes from any one patient's cells — build_completeness
    # gives every patient the same timepoint sequence (same reasoning as completeness_kpis).
    tp_order = {tp: i for i, tp in enumerate(c["timepoint"] for c in grid[0]["cells"])}

    # A set per pair: the field is named n_patients, so one patient missing the same pair
    # twice must still count once.
    by_pair: Dict[Tuple[str, str], set] = {}
    for patient in grid:
        for cell in patient["cells"]:
            for item in cell["items"]:
                if item["state"] == "MISSING":
                    key = (cell["timepoint"], item["modality"])
                    by_pair.setdefault(key, set()).add(patient["patient"])

    gaps = [{"timepoint": tp, "modality": mod,
             "n_patients": len(patients), "patients": sorted(patients)}
            for (tp, mod), patients in by_pair.items()]
    gaps.sort(key=lambda g: (-g["n_patients"],
                             tp_order.get(g["timepoint"], len(tp_order)),
                             g["modality"]))
    return gaps


def completeness_kpis(long_df: pd.DataFrame) -> dict:
    """Cohort-level numbers for a dashboard header — built from completeness_grid so the
    per-patient math (including the vacuous/UNMAPPED convention) never drifts from the grid.
    """
    grid = completeness_grid(long_df)
    if not grid:
        return {"n_patients": 0, "n_complete": 0, "n_incomplete": 0,
                "pct_complete_mean": 0.0, "worst_gap": None, "n_unmapped": 0}

    n_patients = len(grid)
    n_complete = sum(1 for p in grid if p["n_missing"] == 0)
    n_unmapped = sum(1 for p in grid for c in p["cells"] if c["state"] == "UNMAPPED")
    pct_complete_mean = round(sum(p["pct_complete"] for p in grid) / n_patients, 1)

    # The worst gap is the first row of the gap table, by definition — so take it from there
    # rather than counting a second time. The counting loop this replaces incremented once per
    # MISSING *item* while completeness_gaps counts distinct *patients*, so a long_df holding
    # the same (patient, timepoint, modality) twice made one page print "most-missed in 2
    # patients" above a table whose own column said 1. Two definitions of one word is how that
    # happens; there is now one, and the tie-break rules live with it.
    gaps = completeness_gaps(long_df)
    worst_gap: Optional[dict] = None
    if gaps:
        worst_gap = {key: gaps[0][key] for key in ("timepoint", "modality", "n_patients")}

    return {"n_patients": n_patients, "n_complete": n_complete,
            "n_incomplete": n_patients - n_complete, "pct_complete_mean": pct_complete_mean,
            "worst_gap": worst_gap, "n_unmapped": n_unmapped}


def patient_completeness(long_df: pd.DataFrame) -> pd.DataFrame:
    """Per-patient completeness over the *mappable* cells (UNMAPPED patients excluded)."""
    if long_df.empty:
        return pd.DataFrame(columns=["patient", "n_expected", "n_present", "n_missing", "pct_complete"])
    exp = long_df[long_df["expected"] & (long_df["state"] != "UNMAPPED")]
    if exp.empty:
        return pd.DataFrame(columns=["patient", "n_expected", "n_present", "n_missing", "pct_complete"])
    g = exp.groupby("patient")
    out = g.agg(n_expected=("expected", "size"), n_present=("observed", "sum")).reset_index()
    out["n_missing"] = out["n_expected"] - out["n_present"]
    out["pct_complete"] = (100.0 * out["n_present"] / out["n_expected"]).round(1)
    return out.sort_values("patient").reset_index(drop=True)
