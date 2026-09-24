"""Analysis-ready exports: the tables a statistician loads, not the ones a reader scans.

The report's first CSV was one row per patient with each visit's state in a column. It
answered "who is missing what", and nothing a presence analysis needs: it held no date at
all, so an M12 MRI counted as MISSING for a patient included four months before extraction -
a visit that cannot have happened yet. Every missingness rate computed from it was biased
upward for recent patients.

This module builds two tables from the canonical index and the completeness grading:

* :func:`patient_timing` - per-patient dates and data-quality counts, merged into the
  patient-level export;
* :func:`presence_long` - one row per patient x visit x modality, the shape a logistic
  regression, a mixed model or a chi-square on presence expects: a 0/1 outcome, when the
  exam happened relative to baseline and to the protocol target, how much data it holds,
  and whether the visit's window had even closed when the data were extracted.

Pure functions over DataFrames; dates as ISO strings; nothing is rendered here.
"""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .completeness import Protocol, assign_timepoints

#: Column order of the long presence table. Kept explicit: an analyst's script depends on it.
PRESENCE_COLUMNS = [
    "patient_id", "timepoint", "modality",
    "expected", "present", "state",
    "baseline_date", "target_day", "window_start_day", "window_end_day",
    "window_end_date", "window_closed",
    "study_date", "days_from_baseline", "days_from_target",
    "n_studies", "n_series", "n_files",
    "patient_id_source", "protocol", "tolerance_days", "extraction_date",
]


def _parse_date(raw) -> Optional[datetime.date]:
    """DICOM DA (YYYYMMDD) or None - the same leniency as completeness._parse_date, so the
    export and the grading agree on which studies are dated."""
    s = str(raw or "").strip()
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def extraction_date(manifest: Optional[dict]) -> Optional[datetime.date]:
    """The day the index was built - the censoring date for every patient in the cohort."""
    raw = str((manifest or {}).get("generated_utc", "") or "")[:10]
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def run_tag(manifest: Optional[dict]) -> str:
    """A filename-safe tag naming the run: last folder of the scanned root + index time.

    Two exports from two cohorts, or from one cohort on two days, used to both be called
    rt_integrity.csv - the second download silently became "rt_integrity (1).csv" and the
    file no longer said what it was. ``COHORT_BRAIN_20260924-1215`` does.
    """
    import re

    manifest = manifest or {}
    root = str(manifest.get("root", "") or "").replace("\\", "/").rstrip("/")
    name = root.rsplit("/", 1)[-1] if root else ""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")[:40] or "cohort"
    stamp = re.sub(r"[^0-9T]", "", str(manifest.get("generated_utc", "") or ""))[:13]
    stamp = stamp.replace("T", "-") if len(stamp) >= 13 else stamp.replace("T", "")
    return f"{name}_{stamp}" if stamp else name


def _iso(d: Optional[datetime.date]) -> Optional[str]:
    return d.isoformat() if d else None


def patient_timing(table: Optional[pd.DataFrame], protocol: Protocol) -> Dict[str, dict]:
    """Per-patient dates and data-quality counts, keyed by patient id.

    ``baseline_date`` is the earliest parseable StudyDate - the same anchor the completeness
    engine places every visit against, so the export and the grading share one clock.
    """
    if table is None or getattr(table, "empty", True):
        return {}
    tagged = assign_timepoints(table, protocol)
    tagged["_date"] = tagged["study_date"].map(_parse_date)
    out: Dict[str, dict] = {}
    for pid, grp in tagged.groupby(tagged["patient_id"].astype(str)):
        dates = grp["_date"].dropna()
        studies = grp.drop_duplicates("study_uid")
        undated = studies["_date"].isna()
        outside = studies["_date"].notna() & (studies["timepoint"] == "UNMAPPED")
        first = dates.min() if not dates.empty else None
        last = dates.max() if not dates.empty else None
        sources = grp["patient_id_source"].astype(str).unique()
        out[pid] = {
            "baseline_date": _iso(first),
            "last_study_date": _iso(last),
            "followup_days": (last - first).days if first and last else None,
            "patient_id_source": sources[0] if len(sources) == 1 else "mixed",
            "n_studies_undated": int(undated.sum()),
            "n_studies_outside_windows": int(outside.sum()),
        }
    return out


def _window(protocol: Protocol, tp: str) -> Tuple[int, int, int]:
    target = int(protocol.offsets.get(tp, 0))
    tol = int(protocol.tolerance_days)
    return target, target - tol, target + tol


def presence_long(table: Optional[pd.DataFrame], comp_long: Optional[pd.DataFrame],
                  protocol: Protocol, manifest: Optional[dict] = None) -> List[dict]:
    """One record per patient x visit x modality the protocol expects or the data holds.

    ``present`` is the outcome: 1 when an exam of that modality was placed in that visit's
    window, 0 when it was expected and not found, empty when the patient could not be placed
    on the timeline at all (their absence is unknown, not zero). ``window_closed`` says
    whether the visit's window had ended by the extraction date - filter on it before
    computing a missingness rate, or model it, but never ignore it.
    """
    if comp_long is None or getattr(comp_long, "empty", True):
        return []
    cutoff = extraction_date(manifest)
    timing = patient_timing(table, protocol)

    # What each (patient, visit, modality) cell actually holds, from the index itself.
    held: Dict[Tuple[str, str, str], dict] = {}
    if table is not None and not getattr(table, "empty", True):
        tagged = assign_timepoints(table, protocol)
        tagged = tagged[tagged["timepoint"] != "UNMAPPED"].copy()
        tagged["_date"] = tagged["study_date"].map(_parse_date)
        n_files = "n_instances" if "n_instances" in tagged.columns else None
        for (pid, tp, mod), grp in tagged.groupby(
                [tagged["patient_id"].astype(str), "timepoint", "modality"]):
            held[(pid, tp, str(mod))] = {
                "study_date": grp["_date"].dropna().min() if grp["_date"].notna().any() else None,
                "n_studies": int(grp["study_uid"].nunique()),
                "n_series": int(grp["series_uid"].astype(str).replace("", pd.NA).nunique()),
                "n_files": int(grp[n_files].sum()) if n_files else int(len(grp)),
            }

    records: List[dict] = []
    for row in comp_long.to_dict("records"):
        state = str(row["state"])
        if state == "NA":
            continue          # neither expected nor observed: not an observation
        pid, tp, mod = str(row["patient"]), str(row["timepoint"]), str(row["modality"])
        t = timing.get(pid, {})
        base = _parse_date(str(t.get("baseline_date") or "").replace("-", ""))
        target, lo, hi = _window(protocol, tp)
        end = base + datetime.timedelta(days=hi) if base else None
        cell = held.get((pid, tp, mod), {})
        sdate = cell.get("study_date")
        from_base = (sdate - base).days if sdate and base else None
        records.append({
            "patient_id": pid, "timepoint": tp, "modality": mod,
            "expected": bool(row["expected"]),
            "present": None if state == "UNMAPPED" else int(bool(row["observed"])),
            "state": state,
            "baseline_date": _iso(base),
            "target_day": target, "window_start_day": lo, "window_end_day": hi,
            "window_end_date": _iso(end),
            "window_closed": (end <= cutoff) if (end and cutoff) else None,
            "study_date": _iso(sdate),
            "days_from_baseline": from_base,
            "days_from_target": (from_base - target) if from_base is not None else None,
            "n_studies": cell.get("n_studies", 0),
            "n_series": cell.get("n_series", 0),
            "n_files": cell.get("n_files", 0),
            "patient_id_source": t.get("patient_id_source"),
            "protocol": protocol.name,
            "tolerance_days": int(protocol.tolerance_days),
            "extraction_date": _iso(cutoff),
        })
    return records


def missing_due(records: List[dict]) -> Dict[str, int]:
    """Per patient: expected exams missing from a window that had closed at extraction.

    The corrected count - fu_n_missing also counts visits that were not yet due.
    """
    out: Dict[str, int] = {}
    for r in records:
        if r["state"] == "MISSING" and r["window_closed"] is True:
            out[r["patient_id"]] = out.get(r["patient_id"], 0) + 1
    return out
