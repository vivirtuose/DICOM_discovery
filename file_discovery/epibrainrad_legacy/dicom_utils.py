# -*- coding: utf-8 -*-
"""Utilitaires DICOM robustes pour scans NAS folder-level."""
from __future__ import annotations

import re
import warnings
from pathlib import Path

import pandas as pd
from pydicom import dcmread

RTSTRUCT_SOP_UID = "1.2.840.10008.5.1.4.1.1.481.3"
RTDOSE_SOP_UID = "1.2.840.10008.5.1.4.1.1.481.2"
RTPLAN_SOP_UID = "1.2.840.10008.5.1.4.1.1.481.5"

RT_SOP_DEFAULT = {
    RTSTRUCT_SOP_UID: "RTSTRUCT",
    RTDOSE_SOP_UID: "RTDOSE",
    RTPLAN_SOP_UID: "RTPLAN",
}

SOP_CLASS_NAMES = {
    RTSTRUCT_SOP_UID: "RT Structure Set Storage",
    RTDOSE_SOP_UID: "RT Dose Storage",
    RTPLAN_SOP_UID: "RT Plan Storage",
    "1.2.840.10008.5.1.4.1.1.2": "CT Image Storage",
    "1.2.840.10008.5.1.4.1.1.4": "MR Image Storage",
    "1.2.840.10008.5.1.4.1.1.128": "PET Image Storage",
    "1.2.840.10008.5.1.4.1.1.66.1": "Spatial Registration Storage",
    "1.2.840.10008.5.1.4.1.1.66.4": "Segmentation Storage",
}

MODALITY_DEFINITIONS = {
    "MR": "Imagerie par résonance magnétique",
    "CT": "Scanner / tomodensitométrie",
    "RTDOSE": "Dose de radiothérapie",
    "RTSTRUCT": "Structures / contours de radiothérapie",
    "RTPLAN": "Plan de traitement radiothérapie",
    "REG": "Registration DICOM, transformation spatiale entre images",
    "PT": "PET / imagerie de médecine nucléaire",
    "OT": "Other : objet DICOM autre/non classé",
    "SEG": "Segmentation DICOM",
    "SR": "Structured Report DICOM",
    "UNKNOWN": "Modalité non déterminée automatiquement",
}


def ensure_parent(path: str | Path) -> None:
    """Crée le dossier parent d'un chemin si nécessaire."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def is_ignored_file(path: Path) -> bool:
    """Ignore fichiers cachés et DICOMDIR."""
    name = path.name
    return name.startswith(".") or name.upper() == "DICOMDIR"


def read_dicom_header(path: str | Path):
    """Lit uniquement le header DICOM. Retourne None si fichier non lisible."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return dcmread(str(path), stop_before_pixels=True, force=True)
    except Exception:
        return None


def sop_class_uid_from_header(ds) -> str:
    """Retourne le SOPClassUID sous forme texte."""
    if ds is None:
        return ""
    return str(getattr(ds, "SOPClassUID", "") or "").strip()


def sop_class_name_from_uid(uid: str) -> str:
    """Nom humain du SOPClassUID si connu."""
    uid = str(uid or "").strip()
    return SOP_CLASS_NAMES.get(uid, "")


def modality_from_header_only(ds) -> str:
    """Détecte la modalité uniquement depuis le header DICOM.

    Notes
    -----
    Cette fonction est utilisée pour la détection fiable des objets RT dans un
    dossier CT. Elle n'utilise pas le chemin afin d'éviter de classer tout le
    dossier CT en RTDOSE/RTSTRUCT à cause du nom parent.
    """
    if ds is None:
        return "UNKNOWN"

    sop = sop_class_uid_from_header(ds)
    if sop in RT_SOP_DEFAULT:
        return RT_SOP_DEFAULT[sop]

    mod = str(getattr(ds, "Modality", "") or "").upper().strip()
    if mod in {"RTSTRUCT", "RTDOSE", "RTPLAN", "MR", "CT", "REG", "PT", "OT", "SEG", "SR"}:
        return mod

    return "UNKNOWN"


def dicom_datetime_from_header(ds):
    """Retourne une datetime DICOM à partir de Acquisition/Series/Study date-time."""
    if ds is None:
        return pd.NaT

    candidates = [
        ("AcquisitionDate", "AcquisitionTime"),
        ("SeriesDate", "SeriesTime"),
        ("StudyDate", "StudyTime"),
        ("ContentDate", "ContentTime"),
    ]
    for dtag, ttag in candidates:
        d = getattr(ds, dtag, None)
        if not d:
            continue
        t = getattr(ds, ttag, "000000") or "000000"
        d = str(d).strip()
        t = str(t).strip().split(".")[0].ljust(6, "0")[:6]
        dt = pd.to_datetime(d + t, format="%Y%m%d%H%M%S", errors="coerce")
        if pd.notna(dt):
            return dt
    return pd.NaT


def modality_from_header_or_name(ds, path: str | Path) -> str:
    """Détecte la modalité depuis header, SOPClassUID, puis nom de fichier/dossier."""
    mod = modality_from_header_only(ds)
    if mod != "UNKNOWN":
        return mod

    text = str(path).upper()

    # Fallback robuste sur le chemin, incluant typos fréquentes d'exports RT.
    if "RTDOSE" in text or "RT DOSE" in text or "RTDDOSE" in text or re.search(r"(^|[/\\])RD[._-]", text):
        return "RTDOSE"
    if (
        "RTSTRUCT" in text
        or "RT STRUCT" in text
        or "RTSTRUCTURE" in text
        or "RT STRUCTURE" in text
        or "RTSRUCT" in text
        or "RTSRUCTURE" in text
        or re.search(r"(^|[/\\])RS[._-]", text)
    ):
        return "RTSTRUCT"
    if "RTPLAN" in text or "RT PLAN" in text or re.search(r"(^|[/\\])RP[._-]", text):
        return "RTPLAN"
    if "/CT" in text or "\\CT" in text or " CT" in text or text.endswith(".CT") or "CT." in text:
        return "CT"
    if "/MR" in text or "\\MR" in text or " MR" in text or text.endswith(".MR") or "MR." in text or "IRM" in text:
        return "MR"
    return "UNKNOWN"


def sequence_from_text(text: str) -> str:
    """Infère une famille de séquence IRM depuis le texte."""
    t = (text or "").upper()
    if "FLAIR" in t:
        return "FLAIR"
    if any(x in t for x in ["DWI", "DIFF", "ADC", "TRACEW"]):
        return "DWI"
    if "PERF" in t or "PWI" in t:
        return "PERFUSION"
    if "SWI" in t or "SWAN" in t:
        return "SWI"
    if "LOCAL" in t or "SURVEY" in t or "SCOUT" in t:
        return "LOCALIZER"
    if "T2" in t:
        return "T2"
    if "T1" in t and any(x in t for x in ["GADO", "GD", "GAD", "CE", "CONTRAST"]):
        return "T1+Gd"
    if "T1" in t or "MPRAGE" in t or "SPGR" in t:
        return "T1"
    return "AUTRE"


def is_rt_candidate_path(path: str | Path) -> bool:
    """Repère les fichiers RT probables à partir du chemin.

    Cette fonction sert uniquement à PRIORISER la lecture des headers. La vérité
    reste SOPClassUID.
    """
    p = Path(path)
    name = p.name.upper()
    text = str(p).upper()

    rt_tokens = [
        "RTSTRUCT", "RT STRUCT", "RTSTRUCTURE", "RT STRUCTURE",
        "RTSRUCT", "RTSRUCTURE",
        "RTDOSE", "RT DOSE", "RTDDOSE",
        "RTPLAN", "RT PLAN",
    ]
    if any(tok in text for tok in rt_tokens):
        return True

    # Nommage DICOM-RT classique : RS.xxx = RTSTRUCT, RD.xxx = RTDOSE, RP.xxx = RTPLAN.
    if name.startswith(("RS.", "RD.", "RP.", "RS_", "RD_", "RP_", "RS-", "RD-", "RP-")):
        return True

    return False


def representative_sample(files: list[Path], max_headers: int = 3) -> list[Path]:
    """Retourne un échantillon de fichiers à lire pour caractériser un dossier."""
    if not files:
        return []

    files = list(files)
    if len(files) <= max_headers:
        return files

    rt_candidates = [f for f in files if is_rt_candidate_path(f)]
    idx = sorted(set([0, len(files) // 2, len(files) - 1]))
    regular = [files[i] for i in idx]

    out = []
    seen = set()
    for f in rt_candidates + regular:
        key = str(f)
        if key not in seen:
            out.append(f)
            seen.add(key)
    return out


def patient_sort_key(patient_id: str) -> tuple[int, str]:
    """Trie IC 003 avant IC 056 avant IC 100, indépendamment des espaces/tirets."""
    s = str(patient_id)
    m = re.search(r"(\d{1,4})", s)
    return (int(m.group(1)) if m else 10**9, s)


def discover_patient_dirs(nas_root: str | Path) -> list[str]:
    """Liste les dossiers patients IC* triés numériquement."""
    root = Path(nas_root)
    if not root.exists():
        return []

    patients = []
    for p in root.iterdir():
        if p.is_dir() and p.name.upper().startswith("IC"):
            patients.append(p.name.replace("IC-", "IC "))
    return sorted(patients, key=patient_sort_key)


def resolve_patient_root(nas_root: str | Path, patient_id: str) -> Path:
    """Résout un dossier patient malgré les variantes IC 003 / IC-003 / IC_003."""
    root = Path(nas_root)
    variants = [
        patient_id,
        patient_id.replace("IC ", "IC-"),
        patient_id.replace("IC-", "IC "),
        patient_id.replace(" ", "_"),
    ]
    for v in variants:
        p = root / v
        if p.exists():
            return p
    return root / patient_id
