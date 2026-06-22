"""Synthetic DICOM-RT cohort generators (no PHI, header-only).

Everything here is fabricated (random UIDs, ``SYNTHETIC^PHANTOM`` patient name, no pixel
data). Two cohorts are produced:

* :func:`generate_synthetic_cohort` — an RT-chain cohort exercising every branch of the
  integrity engine, with an embedded ground truth (the test oracle);
* :func:`generate_longitudinal_cohort` — a patient x timepoint cohort with follow-up gaps
  and an unexpected modality, for the completeness map.

Lives inside the package (not in ``synth/``) so the CLI ``dicom-discovery demo`` works after a
plain ``pip install``.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

CT_SOP = "1.2.840.10008.5.1.4.1.1.2"
MR_SOP = "1.2.840.10008.5.1.4.1.1.4"
PT_SOP = "1.2.840.10008.5.1.4.1.1.128"
RTSTRUCT_SOP = "1.2.840.10008.5.1.4.1.1.481.3"
RTDOSE_SOP = "1.2.840.10008.5.1.4.1.1.481.2"
RTPLAN_SOP = "1.2.840.10008.5.1.4.1.1.481.5"


@dataclass
class GroundTruth:
    """Expected QC verdict for one synthetic patient (the test oracle)."""

    patient_id: str
    expected_status: str
    expected_codes: Tuple[str, ...]
    description: str


# --------------------------------------------------------------------------- #
# Low-level object builders
# --------------------------------------------------------------------------- #
def _base(sop_class: str, sop_instance: str, patient_id: str, study_uid: str,
          frame_of_reference: Optional[str], modality: str,
          study_date: Optional[str] = None) -> FileDataset:
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = sop_class
    fm.MediaStorageSOPInstanceUID = sop_instance
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = generate_uid()

    ds = FileDataset(None, {}, file_meta=fm, preamble=b"\0" * 128)
    ds.PatientID = patient_id
    ds.PatientName = "SYNTHETIC^PHANTOM"  # never a real name
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = sop_instance
    ds.Modality = modality
    if frame_of_reference is not None:
        ds.FrameOfReferenceUID = frame_of_reference
    ds.StudyDate = study_date or datetime.datetime.now().strftime("%Y%m%d")
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def _ref_for_sequence(frame_of_reference: str) -> Sequence:
    item = Dataset()
    item.FrameOfReferenceUID = frame_of_reference
    return Sequence([item])


def _make_ct(patient_id, study_uid, for_uid, study_date=None):
    return _base(CT_SOP, generate_uid(), patient_id, study_uid, for_uid, "CT", study_date)


def _make_mr(patient_id, study_uid, for_uid, study_date=None):
    return _base(MR_SOP, generate_uid(), patient_id, study_uid, for_uid, "MR", study_date)


def _make_pt(patient_id, study_uid, for_uid, study_date=None):
    return _base(PT_SOP, generate_uid(), patient_id, study_uid, for_uid, "PT", study_date)


def _make_rtstruct(patient_id, study_uid, for_uid, roi_names: List[str], study_date=None) -> FileDataset:
    ds = _base(RTSTRUCT_SOP, generate_uid(), patient_id, study_uid, None, "RTSTRUCT", study_date)
    ds.ReferencedFrameOfReferenceSequence = _ref_for_sequence(for_uid)
    roi_seq = []
    for i, name in enumerate(roi_names, start=1):
        roi = Dataset()
        roi.ROINumber = i
        roi.ROIName = name
        roi.ReferencedFrameOfReferenceUID = for_uid
        roi_seq.append(roi)
    ds.StructureSetROISequence = Sequence(roi_seq)
    return ds


def _make_rtplan(patient_id, study_uid, for_uid, ref_struct_uid: Optional[str],
                 n_fractions: int = 30, study_date=None) -> FileDataset:
    ds = _base(RTPLAN_SOP, generate_uid(), patient_id, study_uid, None, "RTPLAN", study_date)
    ds.ReferencedFrameOfReferenceSequence = _ref_for_sequence(for_uid)
    if ref_struct_uid is not None:
        ref = Dataset()
        ref.ReferencedSOPClassUID = RTSTRUCT_SOP
        ref.ReferencedSOPInstanceUID = ref_struct_uid
        ds.ReferencedStructureSetSequence = Sequence([ref])
    fg = Dataset()
    fg.FractionGroupNumber = 1
    fg.NumberOfFractionsPlanned = n_fractions
    ds.FractionGroupSequence = Sequence([fg])
    return ds


def _make_rtdose(patient_id, study_uid, for_uid, ref_plan_uid: Optional[str], study_date=None) -> FileDataset:
    ds = _base(RTDOSE_SOP, generate_uid(), patient_id, study_uid, for_uid, "RTDOSE", study_date)
    ds.DoseUnits = "GY"
    ds.DoseSummationType = "PLAN"
    if ref_plan_uid is not None:
        ref = Dataset()
        ref.ReferencedSOPClassUID = RTPLAN_SOP
        ref.ReferencedSOPInstanceUID = ref_plan_uid
        ds.ReferencedRTPlanSequence = Sequence([ref])
    return ds


def _save(ds: FileDataset, folder: Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    ds.save_as(str(folder / name), write_like_original=False)


# --------------------------------------------------------------------------- #
# RT-chain integrity cohort (with ground truth)
# --------------------------------------------------------------------------- #
def generate_synthetic_cohort(out_dir: str) -> List[GroundTruth]:
    """Write the RT-chain cohort and return the per-patient ground truth."""
    out = Path(out_dir)
    truth: List[GroundTruth] = []
    full_targets = ["GTV", "CTV", "PTV", "Brainstem", "Cochlea_L"]

    # P001: nominal complete chain, consistent FoR, all targets -> OK
    pid = "P001"; d = out / pid; study = generate_uid(); for_uid = generate_uid()
    struct = _make_rtstruct(pid, study, for_uid, full_targets)
    plan = _make_rtplan(pid, study, for_uid, struct.SOPInstanceUID)
    dose = _make_rtdose(pid, study, for_uid, plan.SOPInstanceUID)
    _save(_make_ct(pid, study, for_uid), d, "ct.dcm")
    _save(struct, d, "rtstruct.dcm"); _save(plan, d, "rtplan.dcm"); _save(dose, d, "rtdose.dcm")
    truth.append(GroundTruth(pid, "OK", (), "complete chain, consistent FoR, all targets present"))

    # P002: multiple RTSTRUCT re-exports; plan references the SECOND (v1 false positive)
    pid = "P002"; d = out / pid; study = generate_uid(); for_uid = generate_uid()
    struct_old = _make_rtstruct(pid, study, for_uid, full_targets)
    struct_new = _make_rtstruct(pid, study, for_uid, full_targets)
    plan = _make_rtplan(pid, study, for_uid, struct_new.SOPInstanceUID)
    dose = _make_rtdose(pid, study, for_uid, plan.SOPInstanceUID)
    _save(_make_ct(pid, study, for_uid), d, "ct.dcm")
    _save(struct_old, d, "rtstruct_v1.dcm"); _save(struct_new, d, "rtstruct_v2.dcm")
    _save(plan, d, "rtplan.dcm"); _save(dose, d, "rtdose.dcm")
    truth.append(GroundTruth(pid, "OK", (),
                             "two RTSTRUCT re-exports, plan links the 2nd (v1 false positive, v2 OK)"))

    # P003: RTPLAN references an ABSENT RTSTRUCT -> genuinely broken
    pid = "P003"; d = out / pid; study = generate_uid(); for_uid = generate_uid()
    struct = _make_rtstruct(pid, study, for_uid, full_targets)
    plan = _make_rtplan(pid, study, for_uid, generate_uid())  # dangling reference
    dose = _make_rtdose(pid, study, for_uid, plan.SOPInstanceUID)
    _save(_make_ct(pid, study, for_uid), d, "ct.dcm")
    _save(struct, d, "rtstruct.dcm"); _save(plan, d, "rtplan.dcm"); _save(dose, d, "rtdose.dcm")
    truth.append(GroundTruth(pid, "WARN", ("PLAN_STRUCT_LINK_BROKEN",),
                             "RTPLAN points at a RTSTRUCT that is not in the patient folder"))

    # P004: orphan RTDOSE referencing an absent RTPLAN
    pid = "P004"; d = out / pid; study = generate_uid(); for_uid = generate_uid()
    struct = _make_rtstruct(pid, study, for_uid, full_targets)
    plan = _make_rtplan(pid, study, for_uid, struct.SOPInstanceUID)
    dose = _make_rtdose(pid, study, for_uid, generate_uid())  # dangling plan reference
    _save(_make_ct(pid, study, for_uid), d, "ct.dcm")
    _save(struct, d, "rtstruct.dcm"); _save(plan, d, "rtplan.dcm"); _save(dose, d, "rtdose.dcm")
    truth.append(GroundTruth(pid, "WARN", ("DOSE_PLAN_LINK_BROKEN",),
                             "RTDOSE points at a RTPLAN that is not in the patient folder"))

    # P005: inconsistent FrameOfReference between RTSTRUCT and RTDOSE
    pid = "P005"; d = out / pid; study = generate_uid()
    for_a = generate_uid(); for_b = generate_uid()
    struct = _make_rtstruct(pid, study, for_a, full_targets)
    plan = _make_rtplan(pid, study, for_a, struct.SOPInstanceUID)
    dose = _make_rtdose(pid, study, for_b, plan.SOPInstanceUID)  # different FoR
    _save(_make_ct(pid, study, for_a), d, "ct.dcm")
    _save(struct, d, "rtstruct.dcm"); _save(plan, d, "rtplan.dcm"); _save(dose, d, "rtdose.dcm")
    truth.append(GroundTruth(pid, "WARN", ("FOR_INCONSISTENT_RT",),
                             "RTDOSE FrameOfReferenceUID differs from RTSTRUCT/RTPLAN"))

    # P006: missing RTDOSE entirely -> incomplete dosimetry
    pid = "P006"; d = out / pid; study = generate_uid(); for_uid = generate_uid()
    struct = _make_rtstruct(pid, study, for_uid, full_targets)
    plan = _make_rtplan(pid, study, for_uid, struct.SOPInstanceUID)
    _save(_make_ct(pid, study, for_uid), d, "ct.dcm")
    _save(struct, d, "rtstruct.dcm"); _save(plan, d, "rtplan.dcm")
    truth.append(GroundTruth(pid, "INCOMPLETE", ("MISSING_RTDOSE",),
                             "no RTDOSE: chain cannot support dose-volume analysis"))

    # P007: complete chain but RTSTRUCT missing the PTV target
    pid = "P007"; d = out / pid; study = generate_uid(); for_uid = generate_uid()
    struct = _make_rtstruct(pid, study, for_uid, ["GTV", "CTV", "Brainstem"])  # no PTV
    plan = _make_rtplan(pid, study, for_uid, struct.SOPInstanceUID)
    dose = _make_rtdose(pid, study, for_uid, plan.SOPInstanceUID)
    _save(_make_ct(pid, study, for_uid), d, "ct.dcm")
    _save(struct, d, "rtstruct.dcm"); _save(plan, d, "rtplan.dcm"); _save(dose, d, "rtdose.dcm")
    truth.append(GroundTruth(pid, "WARN", ("MISSING_TARGET_ROI",),
                             "complete chain but PTV target volume is absent"))

    return truth


# --------------------------------------------------------------------------- #
# Longitudinal cohort (for the completeness map)
# --------------------------------------------------------------------------- #
# Timepoint StudyDates (each timepoint = a distinct Study on a distinct date, so the
# timepoint is derived from StudyDate by the completeness engine, not from the folder).
_BASELINE = datetime.date(2020, 1, 6)
_TP_DAYS = {"baseline": 0, "M3": 90, "M6": 180, "M12": 360}


def _tp_date(tp: str) -> str:
    return (_BASELINE + datetime.timedelta(days=_TP_DAYS[tp])).strftime("%Y%m%d")


def _write_baseline_chain(folder: Path, pid: str, study: str, for_uid: str,
                          date: str, with_dose: bool = True) -> None:
    struct = _make_rtstruct(pid, study, for_uid, ["GTV", "CTV", "PTV"], study_date=date)
    plan = _make_rtplan(pid, study, for_uid, struct.SOPInstanceUID, study_date=date)
    _save(_make_ct(pid, study, for_uid, study_date=date), folder, "ct.dcm")
    _save(_make_mr(pid, study, for_uid, study_date=date), folder, "mr.dcm")
    _save(struct, folder, "rtstruct.dcm")
    _save(plan, folder, "rtplan.dcm")
    if with_dose:
        _save(_make_rtdose(pid, study, for_uid, plan.SOPInstanceUID, study_date=date), folder, "rtdose.dcm")


def _write_followup_mr(out: Path, pid: str, for_uid: str, tp: str) -> None:
    study = generate_uid()  # each timepoint is its own study
    _save(_make_mr(pid, study, for_uid, study_date=_tp_date(tp)), out / pid / tp, "mr.dcm")


def generate_longitudinal_cohort(out_dir: str) -> List[Tuple[str, str]]:
    """Write the longitudinal cohort; return (patient, description) pairs."""
    out = Path(out_dir)
    notes: List[Tuple[str, str]] = []
    bdate = _tp_date("baseline")

    # L001: fully complete across all timepoints.
    pid = "L001"; for_uid = generate_uid()
    _write_baseline_chain(out / pid / "baseline", pid, generate_uid(), for_uid, bdate)
    for tp in ("M3", "M6", "M12"):
        _write_followup_mr(out, pid, for_uid, tp)
    notes.append((pid, "complete at every timepoint -> 100%"))

    # L002: follow-up gap (no MR at M6).
    pid = "L002"; for_uid = generate_uid()
    _write_baseline_chain(out / pid / "baseline", pid, generate_uid(), for_uid, bdate)
    for tp in ("M3", "M12"):
        _write_followup_mr(out, pid, for_uid, tp)
    notes.append((pid, "missing M6 MR (follow-up gap)"))

    # L003: incomplete baseline (no RTDOSE) and dropped before M12.
    pid = "L003"; for_uid = generate_uid()
    _write_baseline_chain(out / pid / "baseline", pid, generate_uid(), for_uid, bdate, with_dose=False)
    for tp in ("M3", "M6"):
        _write_followup_mr(out, pid, for_uid, tp)
    notes.append((pid, "no baseline RTDOSE; missing M12 MR"))

    # L004: baseline only, lost to follow-up.
    pid = "L004"; for_uid = generate_uid()
    _write_baseline_chain(out / pid / "baseline", pid, generate_uid(), for_uid, bdate)
    notes.append((pid, "baseline only, no follow-up imaging"))

    # L005: complete + an EXTRA modality not in the protocol (PT at baseline).
    pid = "L005"; for_uid = generate_uid()
    study_bl = generate_uid()
    _write_baseline_chain(out / pid / "baseline", pid, study_bl, for_uid, bdate)
    _save(_make_pt(pid, study_bl, for_uid, study_date=bdate), out / pid / "baseline", "pt.dcm")
    for tp in ("M3", "M6", "M12"):
        _write_followup_mr(out, pid, for_uid, tp)
    notes.append((pid, "complete + extra PT at baseline (not in protocol)"))

    return notes
