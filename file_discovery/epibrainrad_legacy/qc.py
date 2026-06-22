#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QC file_discovery vs disque.

Compare, pour un patient donné, le nombre de fichiers détectés par
file_discovery avec le nombre réel de fichiers présents dans chaque dossier
sur le NAS.

Point important : le QC compte aussi les dossiers "hybrides", c'est-à-dire
les dossiers qui contiennent à la fois des fichiers DICOM et des sous-dossiers.
C'est indispensable pour certains patients comme IC 092 où le dossier patient
racine contient directement des fichiers CT en plus d'une sous-arborescence MR.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd

from .dicom_utils import is_ignored_file


KEEP_COLUMNS = [
    "patient_id",
    "filepath",
    "folder_name",
    "modality",
    "n_files_in_series",
]


def iter_candidate_dirs_including_root(patient_root: str | Path) -> Iterable[Path]:
    """
    Yield le dossier patient racine puis tous ses sous-dossiers.

    Path.rglob('*') ne retourne pas le dossier racine lui-même. Or certains
    exports DICOM contiennent des fichiers directement à la racine du patient,
    par exemple :

        IC 092/
            CT....dcm
            IC-092/
                .../images/...

    Sans inclure la racine, le QC compte moins de dossiers/fichiers que
    file_discovery alors que file_discovery est correct.
    """
    root = Path(patient_root)
    if not root.exists():
        return

    if root.is_dir():
        yield root

    for path in root.rglob("*"):
        if path.is_dir():
            yield path


def count_direct_files(folder: Path) -> int:
    """
    Compte uniquement les fichiers directement contenus dans un dossier.

    Ne descend pas récursivement dans les sous-dossiers, afin de respecter la
    logique folder-level : 1 dossier contenant des fichiers = 1 entrée série.
    """
    return sum(
        1
        for f in folder.iterdir()
        if f.is_file() and not is_ignored_file(f)
    )


def disk_folder_counts(patient_root: str | Path) -> pd.DataFrame:
    """
    Retourne un DataFrame avec le nombre réel de fichiers par dossier.

    Inclut :
    - dossiers terminaux classiques ;
    - dossiers hybrides avec fichiers + sous-dossiers ;
    - dossier racine patient s'il contient directement des fichiers.
    """
    root = Path(patient_root)
    rows = []

    for folder in iter_candidate_dirs_including_root(root):
        n_files = count_direct_files(folder)
        if n_files == 0:
            continue

        rows.append({
            "filepath": str(folder),
            "disk_folder_name": folder.name,
            "disk_n_files": int(n_files),
        })

    return pd.DataFrame(rows)


def _prepare_discovery_subset(df: pd.DataFrame, patient_id: str) -> pd.DataFrame:
    """Sélectionne les lignes discovery du patient et garantit les colonnes utiles."""
    sub = df[df["patient_id"].astype(str) == str(patient_id)].copy()

    for col in KEEP_COLUMNS:
        if col not in sub.columns:
            sub[col] = pd.NA

    sub = sub[KEEP_COLUMNS].copy()
    sub["n_files_in_series"] = pd.to_numeric(
        sub["n_files_in_series"], errors="coerce"
    )

    return sub


def assign_qc_status(row: pd.Series) -> str:
    has_discovery = pd.notna(row.get("n_files_in_series"))
    has_disk = pd.notna(row.get("disk_n_files"))

    if has_discovery and has_disk:
        try:
            if int(row["n_files_in_series"]) == int(row["disk_n_files"]):
                return "MATCH"
            return "MISMATCH"
        except Exception:
            return "MISMATCH"

    if has_disk and not has_discovery:
        return "ONLY_DISK"

    if has_discovery and not has_disk:
        return "ONLY_DISCOVERY"

    return "UNKNOWN"


def compare_discovery_vs_disk(
    discovery_csv: str | Path,
    patient_id: str,
    patient_root: str | Path,
    out_csv: str | Path,
) -> pd.DataFrame:
    """
    Compare le CSV file_discovery au disque pour un patient.
    """
    discovery_csv = Path(discovery_csv)
    patient_root = Path(patient_root)
    out_csv = Path(out_csv)

    df = pd.read_csv(discovery_csv)
    sub = _prepare_discovery_subset(df, patient_id)
    disk = disk_folder_counts(patient_root)

    if disk.empty:
        disk = pd.DataFrame(columns=["filepath", "disk_folder_name", "disk_n_files"])

    qc = sub.merge(disk, on="filepath", how="outer")

    if "patient_id" not in qc.columns:
        qc["patient_id"] = patient_id
    qc["patient_id"] = qc["patient_id"].fillna(patient_id)

    qc["qc_status"] = qc.apply(assign_qc_status, axis=1)

    # Colonnes lisibles dans un ordre stable
    ordered_cols = [
        "patient_id",
        "filepath",
        "folder_name",
        "disk_folder_name",
        "modality",
        "n_files_in_series",
        "disk_n_files",
        "qc_status",
    ]
    for col in ordered_cols:
        if col not in qc.columns:
            qc[col] = pd.NA
    qc = qc[ordered_cols]

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    qc.to_csv(out_csv, index=False)

    n_disk_folders = len(disk)
    n_discovery_folders = len(sub)
    n_disk_files = int(pd.to_numeric(disk["disk_n_files"], errors="coerce").fillna(0).sum()) if not disk.empty else 0
    n_discovery_files = int(pd.to_numeric(sub["n_files_in_series"], errors="coerce").fillna(0).sum()) if not sub.empty else 0

    print("=" * 80)
    print("QC file_discovery vs disque")
    print("=" * 80)
    print(f"Patient            : {patient_id}")
    print(f"Dossier patient    : {patient_root}")
    print(f"CSV discovery      : {discovery_csv}")
    print(f"Dossiers sur disque        : {n_disk_folders}")
    print(f"Dossiers file_discovery    : {n_discovery_folders}")
    print(f"Fichiers disque total      : {n_disk_files}")
    print(f"Fichiers discovery total   : {n_discovery_files}")
    print("-" * 80)
    print(qc["qc_status"].value_counts(dropna=False).to_string())
    print(f"CSV QC sauvegardé : {out_csv}")

    if (qc["qc_status"] != "MATCH").any():
        print("\nLignes non MATCH à inspecter :")
        print(
            qc.loc[qc["qc_status"] != "MATCH", ordered_cols]
            .to_string(index=False)
        )

    return qc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="QC file_discovery : compare les comptages folder-level au disque."
    )
    parser.add_argument("--discovery-csv", required=True)
    parser.add_argument("--patient-id", required=True)
    parser.add_argument("--patient-root", required=True)
    parser.add_argument("--out-csv", required=True)
    args = parser.parse_args()

    compare_discovery_vs_disk(
        discovery_csv=args.discovery_csv,
        patient_id=args.patient_id,
        patient_root=args.patient_root,
        out_csv=args.out_csv,
    )


if __name__ == "__main__":
    main()
