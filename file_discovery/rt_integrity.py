# -*- coding: utf-8 -*-
"""
rt_integrity.py — Vérification de l'intégrité de la chaîne RT (radiothérapie).

Pour chaque patient, lit UNIQUEMENT les headers DICOM (jamais les pixels) des objets
RT et CT de planification, et vérifie ce qui rend une dosimétrie réellement exploitable :

  1. FrameOfReferenceUID partagé entre RTSTRUCT / RTDOSE / RTPLAN (et le CT de planif) ;
  2. chaînage RTPLAN -> RTSTRUCT et RTDOSE -> RTPLAN via les SOPInstanceUID référencés ;
  3. liste des ROI (StructureSetROISequence) + présence de GTV / CTV / PTV ;
  4. métadonnées dose (DoseUnits, DoseSummationType) et nb de fractions du plan.

Entrée : le DataFrame folder-level produit par le moteur de découverte (colonnes
`patient_id`, `modality`, `example_file`, `filepath`). Aucune relecture complète du NAS :
on ne lit que le fichier représentatif (`example_file`) de chaque objet RT/CT utile.

Sortie : un DataFrame `df_rt_integrity` (une ligne par patient) + un panneau HTML
injectable dans la carte cohorte.
"""
from __future__ import annotations

import html
from typing import Dict, List, Optional

import pandas as pd

try:
    from pydicom import dcmread
    _HAS_PYDICOM = True
except Exception:  # pragma: no cover
    _HAS_PYDICOM = False

RT_SOP = {
    "1.2.840.10008.5.1.4.1.1.481.3": "RTSTRUCT",
    "1.2.840.10008.5.1.4.1.1.481.2": "RTDOSE",
    "1.2.840.10008.5.1.4.1.1.481.5": "RTPLAN",
}
ROI_TARGETS = ("GTV", "CTV", "PTV")


def _seq(ds, name) -> list:
    return list(getattr(ds, name, []) or [])


def read_rt_object(path: str) -> Dict:
    """Lit le header d'un objet RT/CT et extrait les champs d'intégrité. Échec -> {}."""
    if not _HAS_PYDICOM or not path:
        return {}
    try:
        ds = dcmread(str(path), stop_before_pixels=True, force=True)
    except Exception:
        return {}
    sop_class = str(getattr(ds, "SOPClassUID", "") or "")
    modality = RT_SOP.get(sop_class) or str(getattr(ds, "Modality", "") or "").upper()
    info = {
        "path": str(path),
        "modality": modality,
        "sop_instance_uid": str(getattr(ds, "SOPInstanceUID", "") or ""),
        "frame_of_reference": "",
        "roi_names": [],
        "ref_struct_uid": "",
        "ref_plan_uid": "",
        "dose_units": "",
        "dose_sum_type": "",
        "n_fractions": None,
    }
    # FrameOfReferenceUID : direct (CT/MR/RTDOSE) ou via séquence (RTSTRUCT/RTPLAN)
    for_uid = str(getattr(ds, "FrameOfReferenceUID", "") or "")
    if not for_uid:
        rfors = _seq(ds, "ReferencedFrameOfReferenceSequence")
        if rfors:
            for_uid = str(getattr(rfors[0], "FrameOfReferenceUID", "") or "")
    info["frame_of_reference"] = for_uid

    if modality == "RTSTRUCT":
        info["roi_names"] = [str(getattr(r, "ROIName", "") or "") for r in _seq(ds, "StructureSetROISequence")]
    elif modality == "RTPLAN":
        rss = _seq(ds, "ReferencedStructureSetSequence")
        if rss:
            info["ref_struct_uid"] = str(getattr(rss[0], "ReferencedSOPInstanceUID", "") or "")
        fgs = _seq(ds, "FractionGroupSequence")
        if fgs:
            nf = getattr(fgs[0], "NumberOfFractionsPlanned", None)
            info["n_fractions"] = int(nf) if nf not in (None, "") else None
    elif modality == "RTDOSE":
        info["dose_units"] = str(getattr(ds, "DoseUnits", "") or "")
        info["dose_sum_type"] = str(getattr(ds, "DoseSummationType", "") or "")
        rps = _seq(ds, "ReferencedRTPlanSequence")
        if rps:
            info["ref_plan_uid"] = str(getattr(rps[0], "ReferencedSOPInstanceUID", "") or "")
    return info


def build_rt_integrity(df_all: pd.DataFrame, max_ct_reads_per_patient: int = 2) -> pd.DataFrame:
    """Construit le DataFrame d'intégrité RT (une ligne par patient) à partir du df folder-level."""
    if df_all is None or df_all.empty or "patient_id" not in df_all.columns:
        return pd.DataFrame()
    needed = {"modality", "example_file"}
    if not needed.issubset(df_all.columns):
        return pd.DataFrame()

    rows: List[dict] = []
    for pid, sub in df_all.groupby("patient_id"):
        objs: Dict[str, Optional[Dict]] = {"RTSTRUCT": None, "RTDOSE": None, "RTPLAN": None}
        for mod in objs:
            r = sub[sub["modality"] == mod]
            if not r.empty:
                objs[mod] = read_rt_object(str(r.iloc[0]["example_file"]))

        ct = sub[sub["modality"] == "CT"]
        ct_fors = set()
        n_ct = int(len(ct))
        if not ct.empty:
            pref = ct[ct.get("filepath", pd.Series("", index=ct.index)).astype(str).str.upper().str.contains("RT")]
            ct_use = pref if not pref.empty else ct
            for _, cr in ct_use.head(max_ct_reads_per_patient).iterrows():
                ci = read_rt_object(str(cr["example_file"]))
                if ci.get("frame_of_reference"):
                    ct_fors.add(ci["frame_of_reference"])

        s, d, p = objs["RTSTRUCT"], objs["RTDOSE"], objs["RTPLAN"]
        has_ct, has_s, has_d, has_p = n_ct > 0, s is not None, d is not None, p is not None

        rois = s["roi_names"] if s else []
        upper = " ".join(rois).upper()
        roi_flags = {t: (t in upper) for t in ROI_TARGETS}

        rt_fors = {x["frame_of_reference"] for x in (s, d, p) if x and x.get("frame_of_reference")}
        frame_consistent = len(rt_fors) <= 1
        for_ct_match = (not rt_fors) or (not ct_fors) or bool(rt_fors & ct_fors)
        plan_struct_ok = bool(p and s and p["ref_struct_uid"] and p["ref_struct_uid"] == s["sop_instance_uid"])
        dose_plan_ok = bool(d and p and d["ref_plan_uid"] and d["ref_plan_uid"] == p["sop_instance_uid"])

        msgs: List[str] = []
        if not (has_ct and has_s and has_d and has_p):
            manquants = [m for m, ok in [("CT", has_ct), ("RTSTRUCT", has_s), ("RTDOSE", has_d), ("RTPLAN", has_p)] if not ok]
            msgs.append("chaîne RT incomplète (manque " + ", ".join(manquants) + ")")
        if not frame_consistent:
            msgs.append("FrameOfReference RT divergents")
        if rt_fors and ct_fors and not (rt_fors & ct_fors):
            msgs.append("FoR RT ≠ FoR CT planif")
        if has_p and has_s and not plan_struct_ok:
            msgs.append("RTPLAN ne référence pas le RTSTRUCT présent")
        if has_d and has_p and not dose_plan_ok:
            msgs.append("RTDOSE ne référence pas le RTPLAN présent")
        if has_s and not all(roi_flags.values()):
            manq = [t for t in ROI_TARGETS if not roi_flags[t]]
            msgs.append("cibles manquantes: " + ", ".join(manq))

        if not has_s or not has_d:
            status = "INCOMPLET"
        elif msgs:
            status = "WARN"
        else:
            status = "OK"

        rows.append({
            "patient_id": pid,
            "rt_status": status,
            "has_CT": has_ct, "has_RTSTRUCT": has_s, "has_RTDOSE": has_d, "has_RTPLAN": has_p,
            "n_roi": len(rois),
            "roi_GTV": roi_flags["GTV"], "roi_CTV": roi_flags["CTV"], "roi_PTV": roi_flags["PTV"],
            "frame_consistent": frame_consistent,
            "for_ct_match": for_ct_match,
            "plan_links_struct": plan_struct_ok,
            "dose_links_plan": dose_plan_ok,
            "dose_units": (d or {}).get("dose_units", ""),
            "dose_sum_type": (d or {}).get("dose_sum_type", ""),
            "n_fractions": (p or {}).get("n_fractions", None),
            "n_frame_of_reference_rt": len(rt_fors),
            "roi_names": " | ".join(rois[:14]),
            "warnings": " ; ".join(msgs),
        })

    out = pd.DataFrame(rows)
    if not out.empty:
        num = out["patient_id"].astype(str).str.extract(r"(\d+)", expand=False)
        out = out.assign(_n=pd.to_numeric(num, errors="coerce")).sort_values(["_n", "patient_id"]).drop(columns="_n")
    return out


def rt_integrity_panel_html(df: pd.DataFrame) -> str:
    """Panneau HTML récapitulatif (injectable dans la carte cohorte)."""
    if df is None or df.empty:
        return "<div class='panel'><h2>Intégrité RT</h2><p>Aucune donnée RT détectée.</p></div>"
    n = len(df)
    n_ok = int((df["rt_status"] == "OK").sum())
    n_warn = int((df["rt_status"] == "WARN").sum())
    n_inc = int((df["rt_status"] == "INCOMPLET").sum())

    def chk(b):
        return "✅" if bool(b) else "❌"

    def badge(s):
        color = {"OK": "#2e7d32", "WARN": "#c77700", "INCOMPLET": "#b00"}.get(s, "#555")
        return f"<b style='color:{color}'>{s}</b>"

    head = ("<tr><th>Patient</th><th>Statut</th><th>CT</th><th>RS</th><th>RD</th><th>RP</th>"
            "<th>nROI</th><th>GTV</th><th>CTV</th><th>PTV</th><th>FoR cohérent</th>"
            "<th>plan→struct</th><th>dose→plan</th><th>fractions</th><th>Avertissements</th></tr>")
    body = []
    for _, r in df.iterrows():
        body.append(
            "<tr>"
            f"<td>{html.escape(str(r['patient_id']))}</td>"
            f"<td>{badge(r['rt_status'])}</td>"
            f"<td>{chk(r['has_CT'])}</td><td>{chk(r['has_RTSTRUCT'])}</td>"
            f"<td>{chk(r['has_RTDOSE'])}</td><td>{chk(r['has_RTPLAN'])}</td>"
            f"<td>{int(r['n_roi'])}</td>"
            f"<td>{chk(r['roi_GTV'])}</td><td>{chk(r['roi_CTV'])}</td><td>{chk(r['roi_PTV'])}</td>"
            f"<td>{chk(r['frame_consistent'])}</td>"
            f"<td>{chk(r['plan_links_struct'])}</td><td>{chk(r['dose_links_plan'])}</td>"
            f"<td>{'' if pd.isna(r['n_fractions']) else int(r['n_fractions'])}</td>"
            f"<td style='font-size:11px'>{html.escape(str(r['warnings']))}</td>"
            "</tr>"
        )
    return (
        "<div class='panel'><h2>Intégrité de la chaîne RT</h2>"
        f"<p><b>{n}</b> patients — "
        f"<span style='color:#2e7d32'>OK : {n_ok}</span> · "
        f"<span style='color:#c77700'>WARN : {n_warn}</span> · "
        f"<span style='color:#b00'>INCOMPLET : {n_inc}</span>. "
        "Vérifie FrameOfReferenceUID partagé, chaînage RTPLAN→RTSTRUCT→RTDOSE et présence GTV/CTV/PTV.</p>"
        "<table class='summary-table compact'>" + head + "".join(body) + "</table></div>"
    )


def inject_panel_into_html(html_path: str, df: pd.DataFrame) -> bool:
    """Insère le panneau d'intégrité RT juste avant </body> de la carte cohorte."""
    try:
        from pathlib import Path
        p = Path(html_path)
        page = p.read_text(encoding="utf-8")
        panel = rt_integrity_panel_html(df)
        if "</body>" in page:
            page = page.replace("</body>", panel + "\n</body>", 1)
        else:
            page = page + panel
        p.write_text(page, encoding="utf-8")
        return True
    except Exception:
        return False
