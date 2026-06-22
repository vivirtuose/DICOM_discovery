# -*- coding: utf-8 -*-
"""Moteur file_discovery folder-level.

Principe officiel EpiBrainRad : 1 dossier terminal contenant des fichiers = 1 série/acquisition.

Correction RT importante
------------------------
Les objets RTSTRUCT/RTDOSE/RTPLAN peuvent être stockés dans le même dossier que
les coupes CT de dosimétrie. Dans ce cas, résumer le dossier par un seul header CT
masque les fichiers RT minoritaires. Ce module crée donc :
- une ligne pour la série principale non-RT du dossier ;
- une ligne additionnelle par type RT détecté dans ce même dossier.

Le comptage reste cohérent : n_files_in_series de la ligne principale exclut les
fichiers RT déjà représentés par les lignes RT additionnelles.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover
    tqdm = None

from .dicom_utils import (
    ensure_parent,
    is_ignored_file,
    read_dicom_header,
    dicom_datetime_from_header,
    modality_from_header_or_name,
    modality_from_header_only,
    sequence_from_text,
    representative_sample,
    discover_patient_dirs,
    resolve_patient_root,
    is_rt_candidate_path,
    sop_class_uid_from_header,
    sop_class_name_from_uid,
)
from .longitudinal import detect_timepoint_from_path


RT_MODALITIES = {"RTSTRUCT", "RTDOSE", "RTPLAN"}


def _folder_needs_deep_rt_scan(series_dir: Path) -> bool:
    """Détermine si on doit lire tous les headers pour chercher des objets RT.

    Notes
    -----
    Les dossiers de dosimétrie contiennent souvent beaucoup de CT + quelques
    objets RT. Les RTSTRUCT peuvent avoir des noms non informatifs ; il faut donc
    classifier par SOPClassUID et pas seulement par filename.
    """
    text = str(series_dir).upper()
    tokens = [
        " RT", "/RT", "\\RT",
        "DOSIM", "DOSIMETRIE", "RADIOTHER", "RADIOTHERAPY",
        "TREATMENT", "ECLIPSE", "PLAN", "DOSE", "STRUCT",
    ]
    return any(tok in text for tok in tokens)


def _read_headers_for_files(files: list[Path]) -> list[tuple[Path, object]]:
    """Lit les headers DICOM d'une liste de fichiers."""
    headers = []
    for f in files:
        ds = read_dicom_header(f)
        if ds is not None:
            headers.append((f, ds))
    return headers


def _detect_rt_objects_in_folder(series_dir: Path, files: list[Path]) -> tuple[dict[str, list[tuple[Path, object]]], set[str]]:
    """Détecte les fichiers RT dans un dossier par SOPClassUID.

    Retourne
    --------
    rt_headers_by_modality : dict
        Dictionnaire {RTSTRUCT/RTDOSE/RTPLAN: [(path, ds), ...]}.
    rt_file_keys : set[str]
        Chemins des fichiers RT détectés, pour les retirer du comptage non-RT.
    """
    rt_headers_by_modality = {m: [] for m in sorted(RT_MODALITIES)}

    # Toujours lire les candidats évidents par nom.
    candidate_files = [f for f in files if is_rt_candidate_path(f)]

    # Dans les dossiers RT/dosimétrie, lire tous les headers : certains RTSTRUCT
    # n'ont pas de nom RS/RTSTRUCT explicite.
    if _folder_needs_deep_rt_scan(series_dir):
        candidate_files = list(files)

    seen = set()
    selected = []
    for f in candidate_files:
        key = str(f)
        if key not in seen:
            selected.append(f)
            seen.add(key)

    for f, ds in _read_headers_for_files(selected):
        mod = modality_from_header_only(ds)
        if mod in RT_MODALITIES:
            rt_headers_by_modality[mod].append((f, ds))

    rt_file_keys = {str(f) for values in rt_headers_by_modality.values() for f, _ in values}
    rt_headers_by_modality = {k: v for k, v in rt_headers_by_modality.items() if v}
    return rt_headers_by_modality, rt_file_keys


def _build_common_row(
    series_dir: Path,
    patient_id: str,
    patient_root: Path,
    files_count: int,
    headers: list[tuple[Path, object]],
    modality: str,
    metadata_quality: str,
    example_file: Path,
    folder_name_suffix: str = "",
) -> dict:
    """Construit une ligne CSV standardisée."""
    ds0 = headers[0][1] if headers else None
    first_file = headers[0][0] if headers else example_file

    series_desc = str(getattr(ds0, "SeriesDescription", "") or series_dir.name) if ds0 is not None else series_dir.name
    protocol_name = str(getattr(ds0, "ProtocolName", "") or "") if ds0 is not None else ""
    dates = [dicom_datetime_from_header(ds) for _, ds in headers]
    dates = [d for d in dates if pd.notna(d)]
    first_dt = min(dates) if dates else pd.NaT
    last_dt = max(dates) if dates else pd.NaT
    text = " ".join([series_dir.name, series_desc, protocol_name])

    sop_uid = sop_class_uid_from_header(ds0)

    return {
        "patient_id": patient_id,
        "patient_root": str(patient_root),
        "filepath": str(series_dir),
        "folder_name": f"{series_dir.name}{folder_name_suffix}",
        "parent_folder": series_dir.parent.name,
        "modality": modality,
        "series_desc": series_desc,
        "protocol_name": protocol_name,
        "mri_sequence": sequence_from_text(text) if modality == "MR" else "",
        "timepoint_detected": detect_timepoint_from_path(series_dir),
        "n_files_in_series": int(files_count),
        "n_headers_read_series": int(len(headers)),
        "first_file_datetime": first_dt,
        "last_file_datetime": last_dt,
        "example_file": str(first_file),
        "sop_class_uid": sop_uid,
        "sop_class_name": sop_class_name_from_uid(sop_uid),
        "metadata_quality": metadata_quality,
    }


def scan_series_folder(
    series_dir: Path,
    patient_id: str,
    patient_root: Path,
    target: str = "all",
    max_headers: int = 3,
) -> list[dict]:
    """Scanne un dossier et retourne une ou plusieurs lignes discovery.

    Notes
    -----
    Si un dossier contient CT + RTSTRUCT/RTDOSE/RTPLAN, les objets RT sont sortis
    en lignes séparées. Cela corrige le sous-dénombrement des RTSTRUCT.
    """
    files = sorted([p for p in series_dir.iterdir() if p.is_file() and not is_ignored_file(p)])
    if not files:
        return []

    rows: list[dict] = []

    # ---- Détection exhaustive des objets RT quand nécessaire ----
    rt_headers_by_modality, rt_file_keys = _detect_rt_objects_in_folder(series_dir, files)

    for rt_modality, rt_headers in sorted(rt_headers_by_modality.items()):
        if target in {"ct", "mri"}:
            continue
        if target == "rt" or target == "all":
            example_file = rt_headers[0][0]
            rows.append(
                _build_common_row(
                    series_dir=series_dir,
                    patient_id=patient_id,
                    patient_root=patient_root,
                    files_count=len(rt_headers),
                    headers=rt_headers,
                    modality=rt_modality,
                    metadata_quality="rt_object_detected_by_sopclassuid",
                    example_file=example_file,
                    folder_name_suffix=f" [{rt_modality}]",
                )
            )

    # ---- Ligne principale non-RT du dossier ----
    non_rt_files = [f for f in files if str(f) not in rt_file_keys]
    if not non_rt_files:
        return rows

    headers = []
    for f in representative_sample(non_rt_files, max_headers=max_headers):
        ds = read_dicom_header(f)
        if ds is not None:
            headers.append((f, ds))

    ds0 = headers[0][1] if headers else None
    first_file = headers[0][0] if headers else non_rt_files[0]
    modality = modality_from_header_or_name(ds0, first_file)

    if target == "ct" and modality != "CT":
        return rows
    if target == "mri" and modality != "MR":
        return rows
    if target == "rt" and modality not in RT_MODALITIES:
        return rows

    rows.append(
        _build_common_row(
            series_dir=series_dir,
            patient_id=patient_id,
            patient_root=patient_root,
            files_count=len(non_rt_files),
            headers=headers,
            modality=modality,
            metadata_quality="folderlevel_sampled_headers" if headers else "folderlevel_no_readable_header",
            example_file=first_file,
        )
    )

    return rows


def discover_patient(
    patient_id: str,
    nas_root: str | Path,
    target: str = "all",
    max_headers: int = 3,
) -> tuple[pd.DataFrame, dict]:
    """Scanne un patient complet."""
    t0 = time.time()
    patient_root = resolve_patient_root(nas_root, patient_id)
    rows: list[dict] = []
    status = {"patient_id": patient_id, "status": "OK", "seconds": None, "error": ""}

    if not patient_root.exists():
        status.update(status="MISSING", error=f"Dossier introuvable: {patient_root}", seconds=0)
        return pd.DataFrame(), status

    try:
        for root, dirs, files in os.walk(patient_root):
            dirs.sort()
            valid_files = [f for f in files if not f.startswith(".") and f.upper() != "DICOMDIR"]
            if not valid_files:
                continue
            folder_rows = scan_series_folder(
                Path(root),
                patient_id,
                patient_root,
                target=target,
                max_headers=max_headers,
            )
            rows.extend(folder_rows)
    except Exception as exc:
        status.update(status="ERROR", error=str(exc))

    df = pd.DataFrame(rows)
    status["seconds"] = round(time.time() - t0, 2)
    status["n_series"] = int(len(df))
    status["n_files"] = int(df["n_files_in_series"].sum()) if not df.empty else 0
    status["n_rtstruct"] = int((df["modality"] == "RTSTRUCT").sum()) if not df.empty else 0
    status["n_rtdose"] = int((df["modality"] == "RTDOSE").sum()) if not df.empty else 0
    status["n_rtplan"] = int((df["modality"] == "RTPLAN").sum()) if not df.empty else 0
    return df, status


def discover_cohort(
    nas_root: str | Path,
    patients: list[str] | None = None,
    target: str = "all",
    workers: int = 1,
    max_headers: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Scanne une cohorte complète."""
    if not patients:
        patients = discover_patient_dirs(nas_root)

    print(f"\nPatients détectés : {len(patients)}")
    print(patients[:20] + (["..."] if len(patients) > 20 else []))
    print(f"\nDiscovery — {len(patients)} patients | target={target} | mode=flat-folder-level")

    all_df, statuses = [], []

    if workers <= 1:
        iterator = tqdm(patients, desc="Discovery NAS", unit="patient") if tqdm else patients
        for pid in iterator:
            df, st = discover_patient(pid, nas_root, target=target, max_headers=max_headers)
            all_df.append(df)
            statuses.append(st)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(discover_patient, pid, nas_root, target, max_headers): pid for pid in patients}
            iterator = as_completed(futs)
            if tqdm:
                iterator = tqdm(iterator, total=len(futs), desc="Discovery NAS", unit="patient")
            for fut in iterator:
                df, st = fut.result()
                all_df.append(df)
                statuses.append(st)

    df_all = (
        pd.concat([d for d in all_df if d is not None and not d.empty], ignore_index=True)
        if any((d is not None and not d.empty) for d in all_df)
        else pd.DataFrame()
    )
    df_status = pd.DataFrame(statuses)

    if not df_all.empty:
        print(f"\nTOTAL : {len(df_all)} séries | {df_all['patient_id'].nunique()} patients")
        print("\nModalités :")
        print(df_all["modality"].value_counts(dropna=False).to_string())

    return df_all, df_status


def save_csv(df: pd.DataFrame, path: str | Path, label: str = "CSV") -> None:
    """Sauvegarde CSV avec création du dossier parent."""
    path = Path(path)
    ensure_parent(path)
    df.to_csv(path, index=False)
    print(f"{label} sauvegardé : {path}")
