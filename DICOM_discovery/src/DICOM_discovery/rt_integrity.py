"""Radiotherapy (RT) chain integrity QC — v3 (consumes the canonical index).

For each RT **study** (not each patient folder) this checks what makes a dosimetry
usable for research:

  1. a single, shared ``FrameOfReferenceUID`` within the study;
  2. the chain ``RTPLAN -> RTSTRUCT`` and ``RTDOSE -> RTPLAN`` resolved against the
     *referenced* ``SOPInstanceUID`` values, over the set of all present objects;
  3. the presence of GTV / CTV / PTV target volumes;
  4. dose metadata and the planned fraction count.

What changed in v3 (generalization council)
--------------------------------------------
The engine no longer walks the filesystem and no longer groups by patient folder. It
consumes the canonical table from :mod:`DICOM_discovery.indexer` and assesses per
``(patient_id, study_uid)``. This fixes the false ``FOR_INCONSISTENT`` seen on the real
cohort: a re-plan is a *distinct study* with its own frame of reference, not an
inconsistency. Link checks still resolve against the set of all present SOPInstanceUIDs
(the earlier multi-RTSTRUCT fix).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from .indexer import IndexResult, build_index, read_instance
from .tg263 import nonconformant_rois

TARGET_ROIS: Tuple[str, ...] = ("GTV", "CTV", "PTV")
CORE_RT_MODALITIES: Tuple[str, ...] = ("RTSTRUCT", "RTDOSE", "RTPLAN")


class Severity(str, enum.Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class Confidence(str, enum.Enum):
    HIGH = "HIGH"            # derived from referenced UIDs / explicit tags
    HEURISTIC = "HEURISTIC"  # depends on a guess (e.g. which CT is the planning CT)


class Status(str, enum.Enum):
    OK = "OK"
    WARN = "WARN"
    INCOMPLETE = "INCOMPLETE"
    NOT_RT = "NOT_RT"      # study has no RT object at all — out of scope, not a defect
    NO_RT = "NO_RT"        # patient-level: no RT anywhere (imaging only)


@dataclass
class Finding:
    code: str
    severity: Severity
    confidence: Confidence
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value}/{self.confidence.value}] {self.code}: {self.message}"


@dataclass
class RTObject:
    """Header-level view of one RT/CT DICOM object (no pixel data)."""

    path: str
    patient_id: str
    modality: str
    study_uid: str = ""
    study_date: str = ""
    series_uid: str = ""
    sop_instance_uid: str = ""
    frame_of_reference: str = ""
    roi_names: List[str] = field(default_factory=list)
    ref_struct_uids: List[str] = field(default_factory=list)
    ref_plan_uids: List[str] = field(default_factory=list)
    dose_units: str = ""
    dose_sum_type: str = ""
    n_fractions: Optional[int] = None


@dataclass
class ScanResult:
    objects: List[RTObject] = field(default_factory=list)
    errors: List[Dict[str, str]] = field(default_factory=list)


def _as_list(v) -> List[str]:
    if isinstance(v, list):
        return v
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return []
    return [v]


def _rtobject_from_row(row: dict) -> RTObject:
    nf = row.get("n_fractions")
    return RTObject(
        path=str(row.get("path", "")),
        patient_id=str(row.get("patient_id", "")),
        modality=str(row.get("modality", "")),
        study_uid=str(row.get("study_uid", "")),
        study_date=str(row.get("study_date", "")),
        series_uid=str(row.get("series_uid", "")),
        sop_instance_uid=str(row.get("sop_instance_uid", "")),
        frame_of_reference=str(row.get("frame_of_reference", "")),
        roi_names=_as_list(row.get("roi_names")),
        ref_struct_uids=_as_list(row.get("ref_struct_uids")),
        ref_plan_uids=_as_list(row.get("ref_plan_uids")),
        dose_units=str(row.get("dose_units", "") or ""),
        dose_sum_type=str(row.get("dose_sum_type", "") or ""),
        n_fractions=(int(nf) if nf not in (None, "") and not (isinstance(nf, float) and pd.isna(nf)) else None),
    )


def rtobjects_from_table(table: pd.DataFrame) -> List[RTObject]:
    """Build RTObjects from the canonical index table."""
    if table is None or table.empty:
        return []
    return [_rtobject_from_row(r) for r in table.to_dict("records")]


# --------------------------------------------------------------------------- #
# Compatibility shims (single-path / single-tree helpers used by tests)
# --------------------------------------------------------------------------- #
def read_rt_object(path: str, patient_id: str = "") -> Optional[RTObject]:
    """Read one DICOM header into an RTObject, or None if not DICOM/unreadable."""
    rec, _looked = read_instance(path)
    if rec is None:
        return None
    obj = _rtobject_from_row(rec)
    if patient_id:
        obj.patient_id = patient_id
    elif not obj.patient_id:
        obj.patient_id = rec.get("patient_id", "")
    return obj


def scan_rt_cohort(root: str, group_by: str = "dicom") -> ScanResult:
    """Index a tree and return RTObjects + unreadable list (thin wrapper over the indexer)."""
    idx = build_index(root, group_by=group_by)
    return ScanResult(objects=rtobjects_from_table(idx.table), errors=idx.unreadable)


# --------------------------------------------------------------------------- #
# Assessment
# --------------------------------------------------------------------------- #
def assess_patient(patient_id: str, objects: Sequence[RTObject]) -> Tuple[Status, List[Finding]]:
    """Grade one RT chain (the objects of a single study). Returns (status, findings)."""
    by_mod: Dict[str, List[RTObject]] = {}
    for o in objects:
        by_mod.setdefault(o.modality, []).append(o)

    structs = by_mod.get("RTSTRUCT", [])
    doses = by_mod.get("RTDOSE", [])
    plans = by_mod.get("RTPLAN", [])
    cts = by_mod.get("CT", [])

    # A study with no RT object at all is out of scope (e.g. follow-up MR) — not a
    # broken RT chain. Do not assess it; let the caller mark it NOT_RT.
    if not (structs or doses or plans):
        return Status.NOT_RT, []

    findings: List[Finding] = []

    for mod in CORE_RT_MODALITIES:
        if not by_mod.get(mod):
            findings.append(Finding(f"MISSING_{mod}", Severity.ERROR, Confidence.HIGH,
                                    f"no {mod} object found for this study"))
    if not cts:
        findings.append(Finding("MISSING_CT", Severity.WARNING, Confidence.HIGH,
                                "no planning CT found (it may live in a separate archive)"))

    rt_fors = {o.frame_of_reference for o in structs + doses + plans if o.frame_of_reference}
    if len(rt_fors) > 1:
        findings.append(Finding("FOR_INCONSISTENT_RT", Severity.ERROR, Confidence.HIGH,
                                f"RT objects span {len(rt_fors)} distinct FrameOfReferenceUID values"))

    ct_fors = {o.frame_of_reference for o in cts if o.frame_of_reference}
    if rt_fors and ct_fors and not (rt_fors & ct_fors):
        findings.append(Finding("FOR_CT_MISMATCH", Severity.INFO, Confidence.HEURISTIC,
                                "RT FrameOfReference not found among any CT series "
                                "(planning-CT selection is heuristic; verify manually)"))

    struct_uids = {o.sop_instance_uid for o in structs if o.sop_instance_uid}
    plan_refs = {u for p in plans for u in p.ref_struct_uids}
    if plans and structs:
        if not plan_refs:
            findings.append(Finding("PLAN_STRUCT_LINK_UNVERIFIABLE", Severity.INFO, Confidence.HIGH,
                                    "RTPLAN declares no ReferencedStructureSet — link cannot be verified"))
        elif not (plan_refs & struct_uids):
            findings.append(Finding("PLAN_STRUCT_LINK_BROKEN", Severity.ERROR, Confidence.HIGH,
                                    "RTPLAN references a RTSTRUCT SOPInstanceUID absent from this study"))

    plan_uids = {o.sop_instance_uid for o in plans if o.sop_instance_uid}
    dose_refs = {u for d in doses for u in d.ref_plan_uids}
    if doses and plans:
        if not dose_refs:
            findings.append(Finding("DOSE_PLAN_LINK_UNVERIFIABLE", Severity.INFO, Confidence.HIGH,
                                    "RTDOSE declares no ReferencedRTPlan — link cannot be verified"))
        elif not (dose_refs & plan_uids):
            findings.append(Finding("DOSE_PLAN_LINK_BROKEN", Severity.ERROR, Confidence.HIGH,
                                    "RTDOSE references a RTPLAN SOPInstanceUID absent from this study"))

    all_rois = [name for o in structs for name in o.roi_names]
    upper = " ".join(all_rois).upper()
    if structs:
        missing_targets = [t for t in TARGET_ROIS if t not in upper]
        if missing_targets:
            findings.append(Finding("MISSING_TARGET_ROI", Severity.WARNING, Confidence.HIGH,
                                    "target volume(s) absent from RTSTRUCT: " + ", ".join(missing_targets)))

    missing_core = any(not by_mod.get(m) for m in CORE_RT_MODALITIES)
    severities = {f.severity for f in findings}
    if missing_core:
        status = Status.INCOMPLETE
    elif Severity.ERROR in severities or Severity.WARNING in severities:
        status = Status.WARN
    else:
        status = Status.OK
    return status, findings


def _assess_to_row(patient_id: str, study_uid: str, study_date: str,
                   objects: Sequence[RTObject]) -> dict:
    status, findings = assess_patient(patient_id, objects)
    by_mod: Dict[str, List[RTObject]] = {}
    for o in objects:
        by_mod.setdefault(o.modality, []).append(o)
    structs, doses, plans = by_mod.get("RTSTRUCT", []), by_mod.get("RTDOSE", []), by_mod.get("RTPLAN", [])
    all_rois = [name for o in structs for name in o.roi_names]
    upper = " ".join(all_rois).upper()
    codes = {f.code for f in findings}
    return {
        "patient_id": patient_id,
        "study_uid": study_uid,
        "study_date": study_date,
        "rt_status": status.value,
        "has_CT": bool(by_mod.get("CT")),
        "has_RTSTRUCT": bool(structs),
        "has_RTDOSE": bool(doses),
        "has_RTPLAN": bool(plans),
        "n_roi": len(all_rois),
        "roi_GTV": "GTV" in upper,
        "roi_CTV": "CTV" in upper,
        "roi_PTV": "PTV" in upper,
        "frame_consistent": "FOR_INCONSISTENT_RT" not in codes,
        "for_ct_match": "FOR_CT_MISMATCH" not in codes,
        "plan_links_struct": "PLAN_STRUCT_LINK_BROKEN" not in codes,
        "dose_links_plan": "DOSE_PLAN_LINK_BROKEN" not in codes,
        "dose_units": (doses[0].dose_units if doses else ""),
        "dose_sum_type": (doses[0].dose_sum_type if doses else ""),
        "n_fractions": (plans[0].n_fractions if plans else None),
        "n_errors": sum(f.severity == Severity.ERROR for f in findings),
        "n_warnings": sum(f.severity == Severity.WARNING for f in findings),
        "n_info": sum(f.severity == Severity.INFO for f in findings),
        "roi_names": " | ".join(all_rois[:14]),
        "findings": " ; ".join(str(f) for f in findings),
    }


def build_rt_integrity(table: pd.DataFrame) -> pd.DataFrame:
    """Build the integrity table from the canonical index, one row per (patient, study)."""
    if table is None or table.empty:
        return pd.DataFrame()
    rows: List[dict] = []
    for (pid, study), grp in table.groupby([table["patient_id"], table["study_uid"].fillna("")]):
        objs = rtobjects_from_table(grp)
        study_date = str(grp["study_date"].dropna().astype(str).replace("", pd.NA).dropna().min() or "")
        rows.append(_assess_to_row(str(pid), str(study), study_date, objs))
    out = pd.DataFrame(rows)
    if not out.empty:
        num = out["patient_id"].astype(str).str.extract(r"(\d+)", expand=False)
        out = (out.assign(_n=pd.to_numeric(num, errors="coerce"))
                  .sort_values(["_n", "patient_id", "study_date"])
                  .drop(columns="_n").reset_index(drop=True))
    return out


def build_rt_integrity_from_dir(root: str, group_by: str = "dicom") -> Tuple[pd.DataFrame, IndexResult]:
    """Convenience: index a directory tree and assess it in one call."""
    idx = build_index(root, group_by=group_by)
    return build_rt_integrity(idx.table), idx


# --------------------------------------------------------------------------- #
# Patient-level rollup (presentation unit)
# --------------------------------------------------------------------------- #
_STATUS_RANK = {Status.OK: 0, Status.WARN: 1, Status.INCOMPLETE: 2}


def build_rt_rollup(table: pd.DataFrame) -> pd.DataFrame:
    """One verdict per patient, reconciling RT objects across studies by *resolved* UID links.

    The unit of *calculation* stays the study (so within-study FoR consistency is
    preserved, avoiding false FOR_INCONSISTENT between re-plans), but the unit of
    *presentation* is the patient. Cross-study reconciliation is a lookup on the
    referenced-UID columns already in the table — not naive object union (which would
    yield a falsely reassuring OK), so a patient is OK only if a dose→plan→struct link
    actually resolves and the targets exist.
    """
    if table is None or table.empty:
        return pd.DataFrame()
    rows: List[dict] = []
    for pid, grp in table.groupby("patient_id"):
        objs = rtobjects_from_table(grp)
        structs = [o for o in objs if o.modality == "RTSTRUCT"]
        doses = [o for o in objs if o.modality == "RTDOSE"]
        plans = [o for o in objs if o.modality == "RTPLAN"]
        cts = [o for o in objs if o.modality == "CT"]
        rt_objs = structs + doses + plans
        n_studies = grp["study_uid"].fillna("").nunique()

        if not rt_objs:
            rows.append({"patient_id": str(pid), "n_studies": int(n_studies), "n_rt_studies": 0,
                         "rt_status": Status.NO_RT.value, "fragmented": False,
                         "has_CT": bool(cts), "has_RTSTRUCT": False, "has_RTDOSE": False,
                         "has_RTPLAN": False, "roi_GTV": False, "roi_CTV": False, "roi_PTV": False,
                         "reason": "imaging only (no RT objects)", "action": "",
                         "n_roi_nonstandard": 0, "roi_nonstandard": []})
            continue

        rt_studies = {o.study_uid for o in rt_objs}
        fragmented = len(rt_studies) > 1
        struct_uids = {s.sop_instance_uid for s in structs if s.sop_instance_uid}
        plan_uids = {p.sop_instance_uid for p in plans if p.sop_instance_uid}
        plan_struct_resolved = (not (plans and structs)) or any(set(p.ref_struct_uids) & struct_uids for p in plans)
        dose_plan_resolved = (not (doses and plans)) or any(set(d.ref_plan_uids) & plan_uids for d in doses)
        upper = " ".join(name for s in structs for name in s.roi_names).upper()
        roi_flags = {t: (t in upper) for t in TARGET_ROIS}
        roi_nonstandard = nonconformant_rois(name for s in structs for name in s.roi_names)

        # within-study FoR consistency (never across studies — preserves the re-plan fix)
        within_study_for_bad = False
        for _su, sgrp in grp[grp["modality"].isin(CORE_RT_MODALITIES)].groupby(grp["study_uid"].fillna("")):
            fors = {f for f in sgrp["frame_of_reference"].astype(str) if f}
            if len(fors) > 1:
                within_study_for_bad = True

        missing_core = [m for m in CORE_RT_MODALITIES if not by_present(objs, m)]
        status, reasons, actions = Status.OK, [], []
        if missing_core:
            status = Status.INCOMPLETE
            reasons.append("missing " + ", ".join(missing_core))
            actions.append("récupérer le(s) objet(s) manquant(s) sur le PACS")
        elif not (plan_struct_resolved and dose_plan_resolved):
            status = Status.WARN
            reasons.append("RT objects present but reference link does not resolve")
            actions.append("vérifier le lien de référence (plan→struct / dose→plan)")
        if not cts and status != Status.INCOMPLETE:
            status = _worse(status, Status.WARN); reasons.append("no planning CT")
            actions.append("récupérer le CT de planification")
        if within_study_for_bad:
            status = _worse(status, Status.WARN); reasons.append("inconsistent FrameOfReference within a study")
            actions.append("vérifier le repère (FrameOfReference)")
        missing_t = [t for t in TARGET_ROIS if not roi_flags[t]]
        if missing_t and structs:
            status = _worse(status, Status.WARN); reasons.append("missing target(s): " + ", ".join(missing_t))
            actions.append("contourer la/les cible(s) manquante(s) : " + ", ".join(missing_t))
        if fragmented:
            reasons.append(f"RT chain spread across {len(rt_studies)} studies (FRAGMENTED)")
            actions.append("regrouper / vérifier la chaîne RT dispersée")

        rows.append({"patient_id": str(pid), "n_studies": int(n_studies), "n_rt_studies": len(rt_studies),
                     "rt_status": status.value, "fragmented": fragmented,
                     "has_CT": bool(cts), "has_RTSTRUCT": bool(structs), "has_RTDOSE": bool(doses),
                     "has_RTPLAN": bool(plans), "roi_GTV": roi_flags["GTV"], "roi_CTV": roi_flags["CTV"],
                     "roi_PTV": roi_flags["PTV"], "reason": " ; ".join(reasons),
                     "action": " ; ".join(actions),
                     "n_roi_nonstandard": len(roi_nonstandard), "roi_nonstandard": roi_nonstandard})

    out = pd.DataFrame(rows)
    if not out.empty:
        num = out["patient_id"].astype(str).str.extract(r"(\d+)", expand=False)
        out = (out.assign(_n=pd.to_numeric(num, errors="coerce"))
                  .sort_values(["_n", "patient_id"]).drop(columns="_n").reset_index(drop=True))
    return out


#: Review priority — the cases that need work (INCOMPLETE, then WARN) sort first;
#: settled verdicts (NO_RT, then OK) sink to the bottom of the registry.
_REVIEW_RANK = {Status.INCOMPLETE.value: 0, Status.WARN.value: 1,
                Status.NO_RT.value: 2, Status.OK.value: 3}


def order_for_review(rollup_df: pd.DataFrame) -> pd.DataFrame:
    """Order the per-patient rollup as a work queue: WARN/INCOMPLETE on top.

    A QC registry is read to *act*, not to admire — the patients that need
    attention belong at the top. Ties break on the numeric patient id.
    """
    if rollup_df is None or rollup_df.empty:
        return rollup_df
    df = rollup_df.copy()
    rank = df["rt_status"].map(lambda s: _REVIEW_RANK.get(s, 99))
    num = pd.to_numeric(df["patient_id"].astype(str).str.extract(r"(\d+)", expand=False), errors="coerce")
    return (df.assign(_r=rank, _n=num)
              .sort_values(["_r", "_n", "patient_id"])
              .drop(columns=["_r", "_n"]).reset_index(drop=True))


def by_present(objs: Sequence[RTObject], modality: str) -> bool:
    return any(o.modality == modality for o in objs)


def _worse(a: Status, b: Status) -> Status:
    return a if _STATUS_RANK.get(a, 0) >= _STATUS_RANK.get(b, 0) else b
