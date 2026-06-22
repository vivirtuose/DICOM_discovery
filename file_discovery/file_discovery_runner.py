#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
file_discovery_runner.py — Scanner de serveurs médicaux/recherche, institution-agnostic.

Runner minimal, autonome (un seul fichier, aucune dépendance au package d'origine).
Conçu pour scanner N'IMPORTE QUELLE racine (ou liste de racines) sans hypothèse sur
l'organisation du serveur, et produire systématiquement :
  - des inventaires CSV (fichiers, dossiers, patients, DICOM, modalités, manquants,
    doublons, ambiguïtés, erreurs) ;
  - un résumé JSON ;
  - un rapport QC Markdown ;
  - une CARTE INTERACTIVE HTML autonome (Plotly embarqué, pas de serveur, pas de CDN).

Toutes les hypothèses spécifiques à une institution (regex d'ID patient, etc.) sont
configurables via la CLI ou via un PROFILE. Rien n'est codé en dur comme défaut.

Exemple générique :
    python file_discovery_runner.py \
        --root /path/to/server/root \
        --out-dir /path/to/output \
        --patient-regex "IC[\\s_-]?\\d{3}" \
        --make-map

Plusieurs racines / institutions :
    python file_discovery_runner.py \
        --root /server1/DICOM --root /server2/MRI --root /server3/clinical \
        --out-dir /out --profile generic --make-map

Dépendances : pandas (requis). pydicom (optionnel, classification DICOM fine).
              plotly (optionnel, carte interactive ; sinon fallback HTML statique).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# --- dépendances optionnelles, dégradation gracieuse ---------------------------
try:
    import pydicom
    from pydicom import dcmread
    _HAS_PYDICOM = True
except Exception:  # pragma: no cover
    _HAS_PYDICOM = False

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except Exception:  # pragma: no cover
    _HAS_PLOTLY = False


LOG = logging.getLogger("file_discovery")


# =============================================================================
# Profils institution-agnostic — fournissent uniquement des DÉFAUTS, jamais des
# comportements codés en dur. L'utilisateur peut tout surcharger par la CLI.
# =============================================================================
PROFILES: Dict[str, Dict] = {
    "generic": {
        # Très large : « lettres optionnelles + 2-4 chiffres » couvre IC009, P-12, SUBJ_003…
        "patient_regexes": [r"\b([A-Za-z]{1,6})[ _\-]?(\d{2,4})\b", r"\b(sub|subj|patient|pat|case|id)[ _\-]?(\d{1,4})\b"],
        "description": "Profil générique, aucune hypothèse institutionnelle.",
    },
    "epibrainrad": {
        "patient_regexes": [r"\bIC[ _\-]?(\d{1,3})\b"],
        "id_template": "IC {num:03d}",
        "description": "EpiBrainRad / Institut Strauss — identifiants IC 001..IC 099.",
    },
    "dicom_rt": {
        "patient_regexes": [r"\b([A-Za-z]{1,6})[ _\-]?(\d{2,4})\b"],
        "description": "Exports DICOM-RT (CT + RTSTRUCT/RTDOSE/RTPLAN).",
    },
    "neuroimaging": {
        "patient_regexes": [r"\b(sub)[ _\-]?(\d{1,4})\b", r"\b([A-Za-z]{2,6})[ _\-]?(\d{2,4})\b"],
        "description": "Conventions type BIDS (sub-XXX) et IRM.",
    },
    "clinical_excel": {
        "patient_regexes": [r"\b([A-Za-z]{1,6})[ _\-]?(\d{2,4})\b"],
        "description": "Focalisé fichiers cliniques Excel/CSV.",
    },
}


# =============================================================================
# Configuration
# =============================================================================
@dataclass
class DiscoveryConfig:
    """Configuration d'un scan. Tous les chemins sont fournis par l'appelant."""
    roots: List[Path]
    out_dir: Path
    patient_regexes: List[str] = field(default_factory=list)
    profile: str = "generic"
    follow_symlinks: bool = False
    max_depth: int = 64
    make_map: bool = True
    cohort_map: bool = False                # carte cohorte longitudinale (moteur vendored)
    workers: int = 1                        # workers pour le scan cohorte (NAS : 2-3)
    include_clinical: bool = False          # fusionner l'Excel clinique dans la carte cohorte
    clinical_xlsx: Optional[str] = None
    rt_check: bool = False                   # vérification d'intégrité de la chaîne RT
    report: bool = False                     # dashboard HTML "software" (file_discovery_report.html)
    read_dicom: bool = True
    max_dicom_headers: int = 20000          # budget global de lectures de header DICOM
    id_template: Optional[str] = None       # ex "IC {num:03d}" ; sinon normalisation auto
    expected_modalities: Tuple[str, ...] = ("CT", "MR", "RTSTRUCT", "RTDOSE")

    def resolved_patterns(self) -> List["re.Pattern"]:
        pats = list(self.patient_regexes)
        if not pats:
            pats = PROFILES.get(self.profile, PROFILES["generic"])["patient_regexes"]
        out = []
        for p in pats:
            try:
                out.append(re.compile(p, re.IGNORECASE))
            except re.error as exc:
                LOG.warning("Regex patient invalide ignorée %r : %s", p, exc)
        return out


# =============================================================================
# Détection d'identifiant patient (configurable)
# =============================================================================
def _normalize_id(raw: str, groups: Tuple[str, ...], id_template: Optional[str]) -> str:
    """Normalise un identifiant détecté. Garde la valeur brute si pas de motif numérique."""
    nums = [g for g in groups if g and g.isdigit()]
    alphas = [g for g in groups if g and not g.isdigit()]
    if id_template and nums:
        try:
            return id_template.format(num=int(nums[-1]), prefix=(alphas[0] if alphas else "").upper())
        except Exception:
            pass
    if alphas and nums:
        return f"{alphas[0].upper()} {int(nums[-1]):03d}"
    if nums:
        return f"{int(nums[-1]):03d}"
    return raw.strip()


def detect_patient_ids(path_text: str, patterns: List["re.Pattern"],
                       id_template: Optional[str]) -> Tuple[Optional[str], List[str], bool]:
    """
    Détecte le(s) identifiant(s) patient dans un chemin.

    Returns
    -------
    (patient_id, raw_matches, ambiguous)
        patient_id : ID normalisé retenu (le plus fréquent), ou None.
        raw_matches : toutes les valeurs brutes matchées.
        ambiguous : True si plusieurs IDs normalisés distincts coexistent.
    """
    found_norm: List[str] = []
    raw_matches: List[str] = []
    for pat in patterns:
        for m in pat.finditer(path_text):
            raw_matches.append(m.group(0))
            found_norm.append(_normalize_id(m.group(0), m.groups(), id_template))
    if not found_norm:
        return None, [], False
    # ID retenu = le plus fréquent (souvent répété dans plusieurs segments du chemin)
    counts = pd.Series(found_norm).value_counts()
    best = str(counts.index[0])
    ambiguous = counts.shape[0] > 1
    return best, list(dict.fromkeys(raw_matches)), ambiguous


# =============================================================================
# Classification de fichiers (générique)
# =============================================================================
EXT_CATEGORY = {
    ".nii": "nifti", ".gz": "nifti_or_gz", ".mgz": "nifti",
    ".xlsx": "excel", ".xls": "excel", ".xlsm": "excel",
    ".csv": "csv", ".tsv": "csv",
    ".ipynb": "notebook", ".py": "python_script",
    ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".tif": "image", ".tiff": "image", ".bmp": "image",
    ".json": "json", ".txt": "text", ".md": "text",
    ".zip": "archive", ".tar": "archive", ".gz_": "archive", ".7z": "archive", ".rar": "archive",
    ".nrrd": "image_volume", ".mha": "image_volume", ".mhd": "image_volume",
    ".dcm": "dicom", ".ima": "dicom", ".img": "dicom_or_analyze",
}
MASK_TOKENS = ("mask", "seg", "label", "roi", "contour", "gtv", "ctv", "ptv")


def looks_like_dicom(path: Path) -> bool:
    """Détecte un fichier DICOM même sans extension (magic 'DICM' à l'offset 128)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(132)
        if len(head) >= 132 and head[128:132] == b"DICM":
            return True
    except Exception:
        return False
    return path.suffix.lower() in (".dcm", ".ima")


def classify_file(path: Path) -> str:
    """Catégorie de haut niveau d'un fichier (hors header DICOM)."""
    name = path.name.lower()
    suffixes = [s.lower() for s in path.suffixes]
    # nifti compressé .nii.gz
    if name.endswith(".nii.gz") or name.endswith(".nii"):
        cat = "nifti"
    elif suffixes and suffixes[-1] in EXT_CATEGORY:
        cat = EXT_CATEGORY[suffixes[-1]]
    else:
        cat = "unknown"
    # raffinement masque/segmentation
    if cat in ("nifti", "image_volume") and any(t in name for t in MASK_TOKENS):
        return "mask_segmentation"
    return cat


# =============================================================================
# Inspection DICOM légère (jamais de pixels en phase de découverte)
# =============================================================================
RT_SOP = {
    "1.2.840.10008.5.1.4.1.1.481.3": "RTSTRUCT",
    "1.2.840.10008.5.1.4.1.1.481.2": "RTDOSE",
    "1.2.840.10008.5.1.4.1.1.481.5": "RTPLAN",
}
IMG_SOP = {
    "1.2.840.10008.5.1.4.1.1.2": "CT",
    "1.2.840.10008.5.1.4.1.1.4": "MR",
    "1.2.840.10008.5.1.4.1.1.128": "PT",
    "1.2.840.10008.5.1.4.1.1.66.4": "SEG",
    "1.2.840.10008.5.1.4.1.1.66.1": "REG",
}


def inspect_dicom(path: Path) -> Dict[str, str]:
    """Lit le header DICOM (sans pixels) et renvoie les champs clés. Échec -> {}."""
    if not _HAS_PYDICOM:
        return {}
    try:
        ds = dcmread(str(path), stop_before_pixels=True, force=True)
    except Exception:
        return {}
    sop = str(getattr(ds, "SOPClassUID", "") or "").strip()
    modality = RT_SOP.get(sop) or IMG_SOP.get(sop) or str(getattr(ds, "Modality", "") or "").upper().strip()
    return {
        "dicom_modality": modality or "UNKNOWN",
        "sop_class_uid": sop,
        "series_description": str(getattr(ds, "SeriesDescription", "") or ""),
        "study_description": str(getattr(ds, "StudyDescription", "") or ""),
        "patient_id_dicom": str(getattr(ds, "PatientID", "") or ""),
        "series_instance_uid": str(getattr(ds, "SeriesInstanceUID", "") or ""),
        "study_instance_uid": str(getattr(ds, "StudyInstanceUID", "") or ""),
        "frame_of_reference_uid": str(getattr(ds, "FrameOfReferenceUID", "") or ""),
    }


# =============================================================================
# Scanner
# =============================================================================
def scan(cfg: DiscoveryConfig) -> Dict[str, pd.DataFrame]:
    """Scanne toutes les racines en continuant malgré les erreurs. Renvoie les inventaires."""
    patterns = cfg.resolved_patterns()
    file_rows: List[dict] = []
    folder_rows: List[dict] = []
    error_rows: List[dict] = []
    dicom_budget = cfg.max_dicom_headers
    t0 = time.time()

    for root in cfg.roots:
        root = Path(root)
        if not root.exists():
            error_rows.append({"scope": "root", "path": str(root), "error": "racine introuvable"})
            LOG.warning("Racine introuvable : %s", root)
            continue
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=cfg.follow_symlinks, onerror=lambda e: error_rows.append({"scope": "walk", "path": getattr(e, "filename", ""), "error": str(e)})):
            d = Path(dirpath)
            depth = len(d.parts) - root_depth
            if depth > cfg.max_depth:
                dirnames[:] = []
                continue
            dirnames.sort()
            pid, raw, ambig = detect_patient_ids(str(d), patterns, cfg.id_template)
            visible = [f for f in filenames if not f.startswith(".")]
            folder_rows.append({
                "root": str(root), "folder": str(d), "folder_name": d.name,
                "depth": depth, "n_files_direct": len(visible),
                "patient_id": pid, "patient_id_ambiguous": ambig,
                "is_symlink": d.is_symlink(),
            })
            for fn in visible:
                fp = d / fn
                try:
                    st = fp.stat()
                    size = st.st_size
                    is_link = fp.is_symlink()
                except Exception as exc:
                    error_rows.append({"scope": "file", "path": str(fp), "error": str(exc)})
                    continue
                category = classify_file(fp)
                # Ne PAS ouvrir les fichiers à extension connue non-DICOM (archives,
                # Excel, etc.) : sur un montage NAS lent avec de gros .zip, sonder le
                # magic 'DICM' de chaque archive est inutile et coûteux. On ne sonde
                # que les fichiers SANS extension (cas classique des exports DICOM).
                ext = fp.suffix.lower()
                is_dcm = category in ("dicom", "dicom_or_analyze") or (ext == "" and looks_like_dicom(fp))
                rec = {
                    "root": str(root), "filepath": str(fp), "filename": fn,
                    "folder": str(d), "parent_folder": d.parent.name,
                    "ext": fp.suffix.lower(), "category": "dicom" if is_dcm else category,
                    "size_bytes": size, "is_empty": size == 0, "is_symlink": is_link,
                    "patient_id": pid, "patient_id_raw": ";".join(raw), "patient_id_ambiguous": ambig,
                    "dicom_modality": "", "sop_class_uid": "", "series_description": "",
                    "series_instance_uid": "", "study_instance_uid": "",
                }
                if is_dcm and cfg.read_dicom and dicom_budget > 0:
                    info = inspect_dicom(fp)
                    dicom_budget -= 1
                    if info:
                        rec.update({k: info.get(k, "") for k in (
                            "dicom_modality", "sop_class_uid", "series_description",
                            "series_instance_uid", "study_instance_uid")})
                        rec["category"] = "dicom_" + info.get("dicom_modality", "UNKNOWN").lower()
                file_rows.append(rec)

    df_files = pd.DataFrame(file_rows)
    df_folders = pd.DataFrame(folder_rows)
    df_errors = pd.DataFrame(error_rows)
    LOG.info("Scan terminé en %.1fs : %d fichiers, %d dossiers, %d erreurs",
             time.time() - t0, len(df_files), len(df_folders), len(df_errors))
    if cfg.read_dicom and dicom_budget <= 0:
        LOG.warning("Budget de lecture de headers DICOM (%d) atteint : certains fichiers non inspectés.",
                    cfg.max_dicom_headers)
    return _derive_inventories(df_files, df_folders, df_errors, cfg)


def _derive_inventories(df_files, df_folders, df_errors, cfg) -> Dict[str, pd.DataFrame]:
    """Construit tous les inventaires dérivés (patients, dicom, modalités, manquants, doublons, ambigus)."""
    inv: Dict[str, pd.DataFrame] = {
        "files": df_files, "folders": df_folders, "errors": df_errors,
    }

    # --- patients ---
    if not df_files.empty and df_files["patient_id"].notna().any():
        g = df_files.dropna(subset=["patient_id"]).groupby("patient_id")
        df_patients = g.agg(
            n_files=("filepath", "count"),
            n_folders=("folder", "nunique"),
            n_dicom=("category", lambda s: s.str.startswith("dicom").sum()),
            categories=("category", lambda s: ";".join(sorted(set(s)))),
            roots=("root", lambda s: ";".join(sorted(set(s)))),
        ).reset_index()
    else:
        df_patients = pd.DataFrame(columns=["patient_id", "n_files", "n_folders", "n_dicom", "categories", "roots"])
    # Inclure aussi les patients détectés par NOM DE DOSSIER même sans fichier scanné
    # (ex. limite de profondeur, ou dossier patient vide) -> cohorte complète.
    if not df_folders.empty and df_folders["patient_id"].notna().any():
        folder_pids = set(df_folders["patient_id"].dropna().astype(str))
        known = set(df_patients["patient_id"].astype(str)) if not df_patients.empty else set()
        extra = sorted(folder_pids - known)
        if extra:
            add = pd.DataFrame({"patient_id": extra, "n_files": 0, "n_folders": 0,
                                "n_dicom": 0, "categories": "", "roots": ""})
            df_patients = pd.concat([df_patients, add], ignore_index=True)
    inv["patients"] = df_patients

    # --- dicom détaillé ---
    if not df_files.empty:
        dcm = df_files[df_files["category"].astype(str).str.startswith("dicom")].copy()
    else:
        dcm = pd.DataFrame()
    inv["dicom"] = dcm

    # --- modalités ---
    if not dcm.empty and "dicom_modality" in dcm.columns:
        mod = dcm.assign(dicom_modality=dcm["dicom_modality"].replace("", "UNKNOWN")) \
                 .groupby("dicom_modality").size().reset_index(name="n_files")
    else:
        mod = pd.DataFrame(columns=["dicom_modality", "n_files"])
    inv["modalities"] = mod

    # --- modalités par patient (pivot présence) ---
    if not dcm.empty and dcm["patient_id"].notna().any():
        pm = dcm.dropna(subset=["patient_id"]).assign(
            dicom_modality=dcm["dicom_modality"].replace("", "UNKNOWN"))
        present = pm.groupby(["patient_id", "dicom_modality"]).size().unstack(fill_value=0)
        present = (present > 0)
        df_missing = present.reindex(columns=sorted(set(list(present.columns) + list(cfg.expected_modalities))), fill_value=False)
        df_missing_by_patient = df_missing.reset_index()
    else:
        df_missing_by_patient = pd.DataFrame(columns=["patient_id"] + list(cfg.expected_modalities))
    inv["missing_by_patient"] = df_missing_by_patient

    # --- doublons (même nom + même taille) ---
    if not df_files.empty:
        dup = df_files[df_files.duplicated(subset=["filename", "size_bytes"], keep=False)
                       & (df_files["size_bytes"] > 0)]
        df_duplicates = dup.sort_values(["filename", "size_bytes"])[
            ["filename", "size_bytes", "patient_id", "filepath"]] if not dup.empty else \
            pd.DataFrame(columns=["filename", "size_bytes", "patient_id", "filepath"])
    else:
        df_duplicates = pd.DataFrame(columns=["filename", "size_bytes", "patient_id", "filepath"])
    inv["duplicates"] = df_duplicates

    # --- ambigus ---
    if not df_files.empty:
        amb = df_files[df_files["patient_id_ambiguous"] == True]  # noqa: E712
        df_ambiguous = amb[["filepath", "patient_id", "patient_id_raw"]] if not amb.empty else \
            pd.DataFrame(columns=["filepath", "patient_id", "patient_id_raw"])
    else:
        df_ambiguous = pd.DataFrame(columns=["filepath", "patient_id", "patient_id_raw"])
    inv["ambiguous"] = df_ambiguous

    return inv


# =============================================================================
# QC — complétude et avertissements
# =============================================================================
def run_qc(inv: Dict[str, pd.DataFrame]) -> List[dict]:
    """Calcule les avertissements de complétude. Renvoie une liste de dicts (warnings)."""
    warns: List[dict] = []
    miss = inv["missing_by_patient"]
    if not miss.empty:
        def has(col): return col in miss.columns
        for _, r in miss.iterrows():
            pid = r["patient_id"]
            def g(c): return bool(r[c]) if has(c) else False
            if g("RTDOSE") and not g("RTSTRUCT"):
                warns.append({"patient_id": pid, "type": "RTDOSE_sans_RTSTRUCT", "severity": "high"})
            if g("RTSTRUCT") and not g("RTDOSE"):
                warns.append({"patient_id": pid, "type": "RTSTRUCT_sans_RTDOSE", "severity": "medium"})
            if g("CT") and not g("RTSTRUCT"):
                warns.append({"patient_id": pid, "type": "CT_sans_RTSTRUCT", "severity": "low"})
            if g("MR") and not (g("SEG") or g("RTSTRUCT")):
                warns.append({"patient_id": pid, "type": "MRI_sans_masque", "severity": "low"})
    # patients avec clinique mais sans imagerie : approximé via catégories
    pat = inv["patients"]
    if not pat.empty:
        for _, r in pat.iterrows():
            cats = str(r.get("categories", ""))
            if ("excel" in cats or "csv" in cats) and r.get("n_dicom", 0) == 0:
                warns.append({"patient_id": r["patient_id"], "type": "clinique_sans_imagerie", "severity": "low"})
    # fichiers vides
    files = inv["files"]
    if not files.empty:
        n_empty = int(files["is_empty"].sum())
        if n_empty:
            warns.append({"patient_id": "*", "type": f"{n_empty}_fichiers_vides", "severity": "low"})
    # ambigus / erreurs
    if not inv["ambiguous"].empty:
        warns.append({"patient_id": "*", "type": f"{len(inv['ambiguous'])}_fichiers_id_ambigu", "severity": "medium"})
    if not inv["errors"].empty:
        warns.append({"patient_id": "*", "type": f"{len(inv['errors'])}_erreurs_acces", "severity": "high"})
    return warns


# =============================================================================
# Sorties : CSV, JSON, Markdown, carte HTML
# =============================================================================
def export_csv(inv: Dict[str, pd.DataFrame], out_dir: Path) -> None:
    csv_dir = out_dir / "inventories"
    csv_dir.mkdir(parents=True, exist_ok=True)
    for name, df in inv.items():
        df.to_csv(csv_dir / f"df_{name}.csv", index=False)
    LOG.info("Inventaires CSV -> %s", csv_dir)


def export_summary_json(inv, warns, cfg, out_dir: Path) -> dict:
    summary = {
        "roots": [str(r) for r in cfg.roots],
        "profile": cfg.profile,
        "patient_regexes": [p.pattern for p in cfg.resolved_patterns()],
        "n_files": int(len(inv["files"])),
        "n_folders": int(len(inv["folders"])),
        "n_patients": int(len(inv["patients"])),
        "n_dicom": int(len(inv["dicom"])),
        "n_errors": int(len(inv["errors"])),
        "n_warnings": len(warns),
        "modalities": inv["modalities"].set_index("dicom_modality")["n_files"].to_dict() if not inv["modalities"].empty else {},
        "categories": inv["files"]["category"].value_counts().to_dict() if not inv["files"].empty else {},
    }
    (out_dir / "discovery_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def export_markdown(inv, warns, summary, cfg, out_dir: Path) -> None:
    lines = [
        f"# Rapport QC file_discovery\n",
        f"_Profil : `{cfg.profile}` · racines : {', '.join(str(r) for r in cfg.roots)}_\n",
        "## Résumé",
        f"- Fichiers : **{summary['n_files']}**",
        f"- Dossiers : **{summary['n_folders']}**",
        f"- Patients détectés : **{summary['n_patients']}**",
        f"- Fichiers DICOM : **{summary['n_dicom']}**",
        f"- Erreurs d'accès : **{summary['n_errors']}**",
        f"- Avertissements QC : **{summary['n_warnings']}**\n",
        "## Catégories de fichiers",
    ]
    for k, v in sorted(summary["categories"].items(), key=lambda x: -x[1]):
        lines.append(f"- `{k}` : {v}")
    lines.append("\n## Modalités DICOM")
    if summary["modalities"]:
        for k, v in sorted(summary["modalities"].items(), key=lambda x: -x[1]):
            lines.append(f"- `{k}` : {v}")
    else:
        lines.append("- (aucune)")
    lines.append("\n## Avertissements de complétude")
    if warns:
        wdf = pd.DataFrame(warns)
        for sev in ("high", "medium", "low"):
            sub = wdf[wdf["severity"] == sev]
            if not sub.empty:
                lines.append(f"\n### Sévérité {sev}")
                for _, w in sub.iterrows():
                    lines.append(f"- **{w['type']}** — patient `{w['patient_id']}`")
    else:
        lines.append("- Aucun avertissement.")
    (out_dir / "qc_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOG.info("Rapport QC Markdown -> %s", out_dir / "qc_report.md")


def make_interactive_map(inv, warns, cfg, out_dir: Path) -> Optional[Path]:
    """
    Carte interactive AUTONOME (Plotly embarqué) : treemap racine > patient > catégorie,
    + un encart QC. Générée même si le scan a partiellement échoué (nœuds d'avertissement).
    """
    out_html = out_dir / "interactive_file_discovery_map.html"
    files = inv["files"]

    if not _HAS_PLOTLY:
        # Fallback HTML statique minimal, toujours produit.
        rows = "".join(f"<tr><td>{w['severity']}</td><td>{w['type']}</td><td>{w['patient_id']}</td></tr>" for w in warns)
        html = f"<html><head><meta charset='utf-8'><title>file_discovery</title></head><body>" \
               f"<h1>file_discovery — carte (fallback statique)</h1>" \
               f"<p>plotly non disponible. Fichiers={len(files)}, patients={len(inv['patients'])}.</p>" \
               f"<h2>Avertissements</h2><table border=1><tr><th>sévérité</th><th>type</th><th>patient</th></tr>{rows}</table>" \
               f"</body></html>"
        out_html.write_text(html, encoding="utf-8")
        LOG.warning("plotly absent : carte statique de secours -> %s", out_html)
        return out_html

    # Construction des chemins hiérarchiques racine > patient > catégorie
    labels, parents, values, hovers = [], [], [], []
    root_label = "TOUTES RACINES"
    labels.append(root_label); parents.append(""); values.append(0); hovers.append("Racine virtuelle")

    if not files.empty:
        tmp = files.copy()
        tmp["patient_id"] = tmp["patient_id"].fillna("(patient inconnu)")
        tmp["cat"] = tmp["category"].fillna("unknown")
        # niveau racine
        for r, sub in tmp.groupby("root"):
            rid = f"root::{r}"
            labels.append(Path(r).name or r); parents.append(root_label)
            values.append(0); hovers.append(f"{r}<br>{len(sub)} fichiers")
            # renommer le label affiché mais garder un id unique : plotly utilise label,
            # on encode l'unicité via le chemin parent -> on met l'id complet en label caché
        # Pour garantir l'unicité, on reconstruit avec ids explicites :
        labels, parents, values, hovers = [root_label], [""], [0], ["Racine virtuelle"]
        ids = [root_label]
        warn_by_pat = {}
        for w in warns:
            warn_by_pat.setdefault(str(w["patient_id"]), []).append(w["type"])

        for r, sub_r in tmp.groupby("root"):
            rid = f"R::{r}"
            ids.append(rid); labels.append(Path(r).name or r); parents.append(root_label)
            values.append(0); hovers.append(f"{r}<br><b>{len(sub_r)}</b> fichiers")
            for pid, sub_p in sub_r.groupby("patient_id"):
                pidid = f"{rid}||P::{pid}"
                wl = warn_by_pat.get(str(pid), [])
                wtxt = ("<br>⚠ " + ", ".join(wl)) if wl else ""
                ids.append(pidid); labels.append(str(pid)); parents.append(rid)
                values.append(0); hovers.append(f"Patient {pid}<br><b>{len(sub_p)}</b> fichiers{wtxt}")
                for cat, sub_c in sub_p.groupby("cat"):
                    cid = f"{pidid}||C::{cat}"
                    n = len(sub_c)
                    ids.append(cid); labels.append(f"{cat} ({n})"); parents.append(pidid)
                    values.append(int(n)); hovers.append(f"{cat}<br>{n} fichiers")

        fig = go.Figure(go.Treemap(
            ids=ids, labels=labels, parents=parents, values=values,
            branchvalues="remainder", text=hovers, hoverinfo="text",
            maxdepth=3,
        ))
    else:
        # Aucun fichier : carte d'avertissement
        fig = go.Figure(go.Treemap(ids=["root"], labels=["Aucun fichier scanné"], parents=[""], values=[1]))

    n_warn = len(warns)
    title = (f"file_discovery — {len(files)} fichiers · {len(inv['patients'])} patients · "
             f"{len(inv['dicom'])} DICOM · {n_warn} avertissements")
    fig.update_layout(title=title, margin=dict(t=50, l=10, r=10, b=10))

    # QC en annotation HTML sous la figure ; include_plotlyjs=True => AUTONOME (pas de CDN).
    graph_html = fig.to_html(full_html=False, include_plotlyjs=True)
    warn_rows = "".join(
        f"<tr><td>{w['severity']}</td><td>{w['type']}</td><td>{w['patient_id']}</td></tr>" for w in warns)
    full = f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<title>file_discovery — carte interactive</title>
<style>body{{font-family:system-ui,Arial,sans-serif;margin:18px;}}
table{{border-collapse:collapse;margin-top:10px}}td,th{{border:1px solid #ccc;padding:4px 8px;font-size:13px}}
.high{{color:#b00}}.medium{{color:#c70}}.low{{color:#555}}</style></head>
<body>
<h1>Carte interactive file_discovery</h1>
<p>Profil <code>{cfg.profile}</code> — racines : {", ".join(str(r) for r in cfg.roots)}</p>
{graph_html}
<h2>Avertissements QC ({n_warn})</h2>
<table><tr><th>sévérité</th><th>type</th><th>patient</th></tr>{warn_rows or '<tr><td colspan=3>aucun</td></tr>'}</table>
</body></html>"""
    out_html.write_text(full, encoding="utf-8")
    LOG.info("Carte interactive autonome -> %s", out_html)
    return out_html


# =============================================================================
# Carte cohorte longitudinale (réutilise le moteur EpiBrainRad vendored)
# =============================================================================
def _resolve_nas_and_patients(roots: List[Path]):
    """Déduit (nas_root, [patients]) à partir des racines fournies.

    - plusieurs racines de même parent  -> nas_root = parent, patients = noms de dossiers ;
    - une seule racine                  -> nas_root = racine, patients = None (cohorte entière).
    Renvoie (None, None) si indéterminable.
    """
    roots = [Path(r) for r in roots]
    if len(roots) == 1:
        return roots[0], None
    parents = {r.parent for r in roots}
    if len(parents) == 1:
        return roots[0].parent, [r.name for r in roots]
    return None, None


def make_cohort_map_legacy(cfg: "DiscoveryConfig", render_map: bool = True):
    """Découvre la cohorte (moteur vendored) puis, si demandé, rend la carte longitudinale.

    Returns
    -------
    (out_html, df_all) : (Optional[Path], Optional[pd.DataFrame])
        out_html = chemin de la carte si rendue ; df_all = DataFrame folder-level
        (réutilisé par la vérification d'intégrité RT).
    """
    legacy_dir = str(Path(__file__).resolve().parent)
    if legacy_dir not in sys.path:
        sys.path.insert(0, legacy_dir)
    try:
        from epibrainrad_legacy.discovery import discover_cohort
        from epibrainrad_legacy.interactive_maps import make_global_interactive_map
    except Exception as exc:
        LOG.error("Moteur cohorte indisponible (%s) — carte/RT non générées.", exc)
        return None, None

    nas_root, patients = _resolve_nas_and_patients(cfg.roots)
    if nas_root is None:
        LOG.error("Racines à parents multiples : impossible de déduire (nas_root, patients). "
                  "Donne un seul --root NAS, ou des --root de même parent.")
        return None, None
    LOG.info("Découverte cohorte : nas_root=%s | patients=%s | workers=%d",
             nas_root, patients if patients else "(tous)", cfg.workers)

    df_all, _ = discover_cohort(nas_root=str(nas_root), patients=patients,
                                target="all", workers=cfg.workers, max_headers=3)

    if cfg.include_clinical and cfg.clinical_xlsx:
        try:
            from epibrainrad_legacy.clinical_utils import load_clinical_dataset, merge_clinical
            clin = load_clinical_dataset(cfg.clinical_xlsx)
            if clin is not None and not clin.empty:
                df_all = merge_clinical(df_all, clin)
        except Exception as exc:
            LOG.warning("Fusion clinique ignorée : %s", exc)

    if df_all is None or df_all.empty:
        LOG.warning("Aucune donnée discovery — carte/RT non générées.")
        return None, df_all

    out_html = None
    if render_map:
        out_html = cfg.out_dir / "file_discovery_global_cohort.html"
        res = make_global_interactive_map(df_all, out_html)
        LOG.info("Carte cohorte -> %s (patients=%s)", res.get("html_path"), res.get("n_patients"))
    return out_html, df_all


# =============================================================================
# Orchestration
# =============================================================================
def run(cfg: DiscoveryConfig) -> Dict[str, object]:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, object] = {"inventories": None, "warnings": [], "summary": None,
                                 "map": None, "cohort_map": None}

    # Scan générique (file-level) + inventaires CSV + QC + treemap : UNIQUEMENT si demandé.
    # C'est la phase la plus lourde sur un gros NAS (walk + stat de tous les fichiers) ;
    # la carte cohorte n'en a pas besoin (elle a son propre moteur folder-level).
    if cfg.make_map:
        inv = scan(cfg)
        warns = run_qc(inv)
        export_csv(inv, cfg.out_dir)
        summary = export_summary_json(inv, warns, cfg, cfg.out_dir)
        export_markdown(inv, warns, summary, cfg, cfg.out_dir)
        result.update(inventories=inv, warnings=warns, summary=summary,
                      map=make_interactive_map(inv, warns, cfg, cfg.out_dir))

    # Carte cohorte / intégrité RT / dashboard : moteur folder-level dédié.
    if cfg.cohort_map or cfg.rt_check or cfg.report:
        render = cfg.cohort_map or cfg.report          # le dashboard a besoin du graphe cohorte
        cohort_html, df_all = make_cohort_map_legacy(cfg, render_map=render)
        result["cohort_map"] = cohort_html if cfg.cohort_map else None
        rt_df = None
        if (cfg.rt_check or cfg.report) and df_all is not None and not df_all.empty:
            try:
                import rt_integrity
                rt_df = rt_integrity.build_rt_integrity(df_all)
                if rt_df is not None and not rt_df.empty:
                    rt_df.to_csv(cfg.out_dir / "df_rt_integrity.csv", index=False)
                    result["rt_integrity"] = rt_df
                    LOG.info("Intégrité RT -> %s (%d patients)", cfg.out_dir / "df_rt_integrity.csv", len(rt_df))
                    if cohort_html and cfg.cohort_map:
                        rt_integrity.inject_panel_into_html(str(cohort_html), rt_df)
                else:
                    rt_df = None
                    LOG.warning("Aucune donnée RT exploitable — df_rt_integrity non produit.")
            except Exception as exc:
                LOG.error("Vérification RT échouée : %s", exc)
        if cfg.report and cohort_html:
            try:
                import reports
                from datetime import datetime
                meta = [f"Racines : {', '.join(str(r) for r in cfg.roots)}",
                        f"Profil : {cfg.profile}",
                        f"Run : {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
                rep = reports.build_report(cohort_html, rt_df,
                                           cfg.out_dir / "file_discovery_report.html", meta_lines=meta)
                result["report"] = rep
                LOG.info("Dashboard -> %s", rep)
            except Exception as exc:
                LOG.error("Génération du dashboard échouée : %s", exc)

    if not (cfg.make_map or cfg.cohort_map or cfg.rt_check or cfg.report):
        LOG.warning("Aucune sortie demandée (--make-map / --cohort-map / --rt-check / --report).")
    return result


def build_config_from_args(args) -> DiscoveryConfig:
    prof = PROFILES.get(args.profile, PROFILES["generic"])
    return DiscoveryConfig(
        roots=[Path(r) for r in args.root],
        out_dir=Path(args.out_dir),
        patient_regexes=list(args.patient_regex or []),
        profile=args.profile,
        follow_symlinks=args.follow_symlinks,
        max_depth=args.max_depth,
        make_map=args.make_map,
        cohort_map=args.cohort_map,
        workers=args.workers,
        include_clinical=args.include_clinical,
        clinical_xlsx=args.clinical_xlsx,
        rt_check=args.rt_check,
        report=args.report,
        read_dicom=not args.no_dicom,
        max_dicom_headers=args.max_dicom_headers,
        id_template=args.id_template or prof.get("id_template"),
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Scanner de serveurs médicaux/recherche, institution-agnostic (runner minimal autonome).")
    p.add_argument("--root", action="append", required=True, help="Racine à scanner (répéter pour plusieurs).")
    p.add_argument("--out-dir", required=True, help="Dossier de sortie.")
    p.add_argument("--patient-regex", action="append", default=[],
                   help="Regex d'ID patient (répéter pour plusieurs). Surcharge le profil.")
    p.add_argument("--profile", default="generic", choices=sorted(PROFILES.keys()),
                   help="Profil de défauts (generic, epibrainrad, dicom_rt, neuroimaging, clinical_excel).")
    p.add_argument("--id-template", default=None, help="Gabarit d'ID, ex 'IC {num:03d}'.")
    p.add_argument("--follow-symlinks", action="store_true", help="Suivre les liens symboliques.")
    p.add_argument("--max-depth", type=int, default=64, help="Profondeur max de descente.")
    p.add_argument("--max-dicom-headers", type=int, default=20000, help="Budget global de lectures de headers DICOM.")
    p.add_argument("--no-dicom", action="store_true", help="Ne pas lire les headers DICOM.")
    p.add_argument("--make-map", action="store_true", help="Générer la carte interactive (treemap structurel autonome).")
    p.add_argument("--cohort-map", action="store_true",
                   help="Générer la carte cohorte longitudinale (date × patient × modalité) via le moteur EpiBrainRad vendored.")
    p.add_argument("--workers", type=int, default=1, help="Workers pour le scan cohorte (NAS : 2-3).")
    p.add_argument("--include-clinical", action="store_true", help="Fusionner l'Excel clinique dans la carte cohorte.")
    p.add_argument("--clinical-xlsx", default=None, help="Chemin de l'Excel clinique (avec --include-clinical).")
    p.add_argument("--rt-check", action="store_true",
                   help="Vérifier l'intégrité de la chaîne RT (FrameOfReference, chaînage RTPLAN→RTSTRUCT→RTDOSE, "
                        "ROI GTV/CTV/PTV) -> df_rt_integrity.csv + panneau dans la carte cohorte.")
    p.add_argument("--report", action="store_true",
                   help="Générer le dashboard HTML 'software' (file_discovery_report.html) : en-tête, "
                        "KPI, onglets, tableau RT triable/filtrable/exportable. Implique la carte cohorte.")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not _HAS_PYDICOM:
        LOG.warning("pydicom absent : classification DICOM dégradée (extension/magic seulement).")
    cfg = build_config_from_args(args)
    res = run(cfg)
    s = res.get("summary")
    if s:
        print(f"\nOK — {s['n_files']} fichiers · {s['n_patients']} patients · "
              f"{s['n_dicom']} DICOM · {s['n_warnings']} avertissements")
    print(f"Sorties : {cfg.out_dir}")
    if res.get("map"):
        print(f"Carte interactive (treemap) : {res['map']}")
    if res.get("cohort_map"):
        print(f"Carte cohorte longitudinale : {res['cohort_map']}")
    rt = res.get("rt_integrity")
    if rt is not None and not rt.empty:
        vc = rt["rt_status"].value_counts().to_dict()
        print(f"Intégrité RT : {cfg.out_dir}/df_rt_integrity.csv "
              f"(OK={vc.get('OK',0)} · WARN={vc.get('WARN',0)} · INCOMPLET={vc.get('INCOMPLET',0)})")
    if res.get("report"):
        print(f"Dashboard : {res['report']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
