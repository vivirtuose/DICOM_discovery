#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Entrée CLI du file_discovery modulaire EpiBrainRad.

Mode unique officiel : flat-folder-level.
Il n'y a volontairement plus d'option --layout.
"""
from __future__ import annotations
import argparse
import time
from pathlib import Path

from .discovery import discover_cohort, save_csv
from .dicom_utils import discover_patient_dirs
from .clinical_utils import load_clinical_dataset, merge_clinical
from .interactive_maps import make_all_patient_maps, make_global_interactive_map

DEFAULT_NAS_ROOT = "/mnt/NAS2418_RADT/datasets/clinical_trials/RADIO-AIDE_full/DICOM"
DEFAULT_CLINICAL_XLSX = "data/Base_donnee_EPIBRAIN_A_JOUR_042026.xlsx"
DEFAULT_OUT_CSV = "output_epi_segmentation/df_discovery_folderlevel.csv"
DEFAULT_INDEX_CSV = "output_epi_segmentation/master_dicom_index.csv"
DEFAULT_INTERACTIVE_DIR = "output_epi_segmentation/interactive_maps"


def parse_args():
    p = argparse.ArgumentParser(description="EpiBrainRad file_discovery modulaire — mode unique flat-folder-level")
    p.add_argument("--target", choices=["all", "rt", "ct", "mri"], default="all", help="Type de données à garder après scan.")
    p.add_argument("--nas-root", default=DEFAULT_NAS_ROOT, help="Racine NAS contenant les dossiers patients IC xxx.")
    p.add_argument("--patients", nargs="*", default=None, help="Patients à scanner, ex: 'IC 003'. Ignoré si --make-global-map.")
    p.add_argument("--workers", type=int, default=1, help="Nombre de workers. Recommandé NAS : 2 ou 3.")
    p.add_argument("--max-headers", type=int, default=3, help="Headers lus par dossier série. Le comptage fichiers reste exhaustif.")
    p.add_argument("--include-clinical", action="store_true", help="Fusionner le fichier clinique Excel.")
    p.add_argument("--clinical-xlsx", default=DEFAULT_CLINICAL_XLSX, help="Chemin vers l'Excel clinique.")
    p.add_argument("--make-interactive-maps", action="store_true", help="Créer une carte HTML par patient scanné.")
    p.add_argument("--make-global-map", action="store_true", help="Scanner toute la cohorte disponible dans nas-root et créer la carte globale.")
    p.add_argument("--interactive-dir", default=DEFAULT_INTERACTIVE_DIR, help="Dossier de sortie des HTML interactifs.")
    p.add_argument("--out-csv", default=DEFAULT_OUT_CSV, help="CSV principal de sortie.")
    p.add_argument("--index-csv", default=DEFAULT_INDEX_CSV, help="Index CSV master sauvegardé en plus du CSV principal.")
    return p.parse_args()


def main():
    args = parse_args()
    t0 = time.time()
    clinical = None
    if args.include_clinical:
        print(f"\nChargement clinique : {args.clinical_xlsx}")
        clinical = load_clinical_dataset(args.clinical_xlsx)
        print(f"Patients cliniques chargés : {clinical['patient_id'].nunique() if clinical is not None and not clinical.empty else 0}")

    patients = None if args.make_global_map else args.patients
    if args.make_global_map:
        print("\nOption --make-global-map activée : scan complet de tous les patients disponibles.")
        patients = discover_patient_dirs(args.nas_root)

    df_all, df_status = discover_cohort(
        nas_root=args.nas_root,
        patients=patients,
        target=args.target,
        workers=args.workers,
        max_headers=args.max_headers,
    )

    if args.include_clinical and clinical is not None and not clinical.empty:
        df_all = merge_clinical(df_all, clinical)

    if not df_all.empty:
        save_csv(df_all, args.index_csv, "Index CSV")
        save_csv(df_all, args.out_csv, "CSV principal")
    if not df_status.empty and (df_status["status"] != "OK").any():
        save_csv(df_status[df_status["status"] != "OK"], Path(args.out_csv).with_name("df_discovery_artifacts.csv"), "CSV artifacts")

    if args.make_interactive_maps and not df_all.empty:
        print("\nGénération des cartes interactives par patient...")
        maps = make_all_patient_maps(df_all, args.interactive_dir)
        print(maps["status"].value_counts(dropna=False).to_string())

    if args.make_global_map and not df_all.empty:
        print("\nGénération de la carte globale Plotly de cohorte...")
        out_html = Path(args.interactive_dir) / "file_discovery_global_cohort.html"
        res = make_global_interactive_map(df_all, out_html)
        print(f"Carte globale générée : {res['html_path']}")
        print(f"Patients dans la carte : {res['n_patients']}")

    print(f"\nDurée totale : {time.time() - t0:.1f}s")
    return df_all


if __name__ == "__main__":
    main()
