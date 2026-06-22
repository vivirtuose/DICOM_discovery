# -*- coding: utf-8 -*-
"""Cartes interactives Plotly patient-level et cohorte.

Version corrigée CT/RT timestamps :
- aucun fallback artificiel en 1900/1901 ;
- si le CSV ne contient pas de date pour CT/RTDOSE/RTSTRUCT/RTPLAN,
  lecture directe des tags DICOM du fichier example_file ou d'un fichier du dossier ;
- fallback final par date extraite du chemin uniquement si un pattern YYYYMMDD est présent ;
- points réellement sans date exclus du graphe et listés dans un tableau HTML ;
- RTSTRUCT affichés en diamant ;
- léger décalage visuel RTDOSE / RTPLAN / RTSTRUCT seulement sur vraie date ;
- tri numérique des patients forcé et réappliqué après filtrage Plotly.
"""
from __future__ import annotations

from pathlib import Path
import html
import json
import re
from datetime import datetime
import numpy as np
import pandas as pd
import plotly.graph_objects as go

try:
    import pydicom
except Exception:  # pragma: no cover
    pydicom = None

from .dicom_utils import ensure_parent, MODALITY_DEFINITIONS
from .clinical_utils import patient_clinical_table_html, cohort_clinical_summary_html
from .longitudinal import timepoint_sort_key, TIMEPOINT_DISPLAY_ORDER


MODALITY_COLORS = {
    "MR": "#2ECC71",
    "CT": "#3498DB",
    "RTDOSE": "#E74C3C",
    "RTSTRUCT": "#9B59B6",
    "RTPLAN": "#F39C12",
    "REG": "#95A5A6",
    "PT": "#1ABC9C",
    "OT": "#7F8C8D",
    "SEG": "#8E44AD",
    "SR": "#34495E",
    "UNKNOWN": "#7F8C8D",
}

RT_MODALITIES = ["RTDOSE", "RTSTRUCT", "RTPLAN"]
DATELESS_RT_CT_MODALITIES = ["CT", "RTDOSE", "RTSTRUCT", "RTPLAN"]

CSS = """
body { font-family: Arial, Helvetica, sans-serif; margin: 24px; color: #1f2d3d; background: #ffffff; }
h1 { margin-bottom: 6px; color: #243B63; }
h2 { color: #243B63; }
.subtitle { margin-bottom: 12px; color: #3b4a5a; font-size: 14px; }
.panel { border: 1px solid #d7dde8; border-radius: 8px; padding: 14px; margin-bottom: 22px; box-shadow: 0 1px 4px rgba(0,0,0,0.06); }
.summary-table { border-collapse: collapse; width: 900px; max-width: 100%; font-size: 13px; margin-bottom: 16px; }
.summary-table.compact { width: 520px; display: inline-table; vertical-align: top; margin-right: 22px; }
.summary-table.wide { width: 100%; }
.summary-table th { background: #eef2f7; text-align: left; padding: 8px; border: 1px solid #cfd8e3; }
.summary-table td { padding: 7px 8px; border: 1px solid #d9e2ec; vertical-align: top; }
.summary-table tr:nth-child(even) td { background: #f8fafc; }
.legend-box { font-size: 13px; line-height: 1.45; }
.warn { background:#fff7ed; border-left:4px solid #f97316; padding:10px; margin:10px 0; }
.ok { background:#f0fdf4; border-left:4px solid #22c55e; padding:10px; margin:10px 0; }
.mono { font-family: monospace; font-size: 12px; }
"""


def _escape(v) -> str:
    if pd.isna(v):
        return ""
    return html.escape(str(v))


def _patient_num(value) -> float:
    m = re.search(r"(\d{1,4})", str(value))
    return float(m.group(1)) if m else np.inf


def _patient_number(patient_id) -> int:
    m = re.search(r"(\d+)", str(patient_id))
    return int(m.group(1)) if m else 9999


def _sorted_patient_order_desc(df: pd.DataFrame) -> list[str]:
    if df is None or df.empty or "patient_id" not in df.columns:
        return []
    return sorted(
        df["patient_id"].dropna().astype(str).unique().tolist(),
        key=_patient_number,
        reverse=True,
    )


def _sort_patients_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "patient_id" not in df.columns:
        return df
    out = df.copy()
    out["_patient_num_sort"] = out["patient_id"].map(_patient_num)
    out = out.sort_values(["_patient_num_sort", "patient_id"]).drop(columns="_patient_num_sort")
    return out


def modality_legend_html() -> str:
    rows = []
    for mod, definition in MODALITY_DEFINITIONS.items():
        color = MODALITY_COLORS.get(mod, "#7F8C8D")
        symbol = "◆" if mod == "RTSTRUCT" else "●"
        rows.append(
            "<tr>"
            f"<td><span style='color:{color};font-size:16px;text-shadow:0 0 1px #333;'>{symbol}</span> "
            f"<b>{html.escape(mod)}</b></td>"
            f"<td>{html.escape(definition)}</td>"
            "</tr>"
        )
    return "<div class='panel legend-box'><h2>Légende des modalités DICOM</h2><table class='summary-table'>" + "".join(rows) + "</table></div>"


def _parse_dicom_date_time(date_value, time_value=None):
    if date_value is None or pd.isna(date_value):
        return None
    d = str(date_value).strip()
    if not d or d.lower() in {"nan", "none", "nat"}:
        return None

    t = "000000" if time_value is None or pd.isna(time_value) else str(time_value).strip()
    t = t.split(".")[0].ljust(6, "0")[:6]

    for fmt, txt in [("%Y%m%d%H%M%S", d + t), ("%Y%m%d", d)]:
        try:
            return pd.Timestamp(datetime.strptime(txt, fmt))
        except Exception:
            pass
    return None


def _is_valid_cohort_datetime(dt) -> bool:
    """Filtre les dates sentinelles/fausses qui polluent la carte.

    Certains objets RT/CT exportés par Eclipse ou copiés depuis un autre système
    portent des dates DICOM de type 19000101/19010101. Ce ne sont pas des
    dates d'examen : ce sont des valeurs par défaut. Elles ne doivent jamais
    être affichées ni servir au positionnement de la carte.
    """
    if dt is None or pd.isna(dt):
        return False
    try:
        ts = pd.Timestamp(dt)
    except Exception:
        return False
    # Cohorte EpiBrainRad : les vraies acquisitions observées commencent en 2017.
    # On garde une marge large à partir de 2000 pour ne pas être trop agressif.
    if ts.year < 2000 or ts.year > 2100:
        return False
    return True


def _dicom_datetime_from_dataset(ds):
    """Retourne (timestamp, source_tag) depuis les tags DICOM les plus utiles.

    Important : on ne s'arrête pas sur une date invalide. Si ContentDate vaut
    19000101 mais StudyDate/SeriesDate est correct, on ignore ContentDate et on
    continue.
    """
    pairs = [
        ("ContentDate", "ContentTime"),
        ("InstanceCreationDate", "InstanceCreationTime"),
        ("StructureSetDate", "StructureSetTime"),
        ("RTPlanDate", "RTPlanTime"),
        ("StudyDate", "StudyTime"),
        ("SeriesDate", "SeriesTime"),
        ("AcquisitionDate", "AcquisitionTime"),
    ]
    invalid_seen = []
    for date_tag, time_tag in pairs:
        dt = _parse_dicom_date_time(getattr(ds, date_tag, None), getattr(ds, time_tag, None))
        if dt is None:
            continue
        if _is_valid_cohort_datetime(dt):
            return dt, f"DICOM {date_tag}+{time_tag}"
        invalid_seen.append(f"{date_tag}={dt}")
    if invalid_seen:
        return pd.NaT, "dates DICOM invalides ignorées: " + "; ".join(invalid_seen[:3])
    return pd.NaT, "date absente"


def _dicom_datetime_from_file(path: str | Path):
    """Lit uniquement l'en-tête d'un fichier DICOM et extrait une date fiable."""
    if pydicom is None:
        return pd.NaT, "pydicom indisponible"

    p = Path(str(path))
    if not p.exists() or not p.is_file():
        return pd.NaT, "fichier absent"

    try:
        ds = pydicom.dcmread(
            str(p),
            stop_before_pixels=True,
            force=True,
            specific_tags=[
                "SOPClassUID",
                "Modality",
                "ContentDate", "ContentTime",
                "InstanceCreationDate", "InstanceCreationTime",
                "StructureSetDate", "StructureSetTime",
                "RTPlanDate", "RTPlanTime",
                "StudyDate", "StudyTime",
                "SeriesDate", "SeriesTime",
                "AcquisitionDate", "AcquisitionTime",
            ],
        )
        return _dicom_datetime_from_dataset(ds)
    except Exception as exc:
        return pd.NaT, f"lecture DICOM impossible: {type(exc).__name__}"


def _date_from_path_text(text) -> tuple[pd.Timestamp, str]:
    """Fallback faible mais utile : récupère YYYYMMDD depuis un chemin/dossier."""
    if text is None or pd.isna(text):
        return pd.NaT, "date absente"
    s = str(text)
    m = re.search(r"(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})(?!\d)", s)
    if not m:
        return pd.NaT, "date absente"
    try:
        return pd.Timestamp(f"{m.group(1)}-{m.group(2)}-{m.group(3)} 12:00:00"), "date extraite du chemin"
    except Exception:
        return pd.NaT, "date absente"


def _iter_candidate_files(folder_or_file: str | Path, modality: str | None = None, max_files: int = 40):
    """Retourne quelques fichiers candidats à lire quand example_file est absent.

    Priorité : noms RT/CT typiques, puis premiers fichiers du dossier.
    """
    p = Path(str(folder_or_file))
    if p.is_file():
        yield p
        return
    if not p.exists() or not p.is_dir():
        return

    mod = str(modality or "").upper()
    if mod == "CT":
        patterns = ["CT*", "*.dcm", "IM*", "*image*"]
    elif mod == "RTSTRUCT":
        patterns = ["RS*", "*RTSTRUCT*", "*STRUCT*", "*.dcm", "IM*"]
    elif mod == "RTDOSE":
        patterns = ["RD*", "*RTDOSE*", "*DOSE*", "*.dcm", "IM*"]
    elif mod == "RTPLAN":
        patterns = ["RP*", "*RTPLAN*", "*PLAN*", "*.dcm", "IM*"]
    else:
        patterns = ["*.dcm", "IM*", "*"]

    seen = set()
    n = 0
    for pat in patterns:
        for f in p.rglob(pat):
            if not f.is_file() or f in seen:
                continue
            seen.add(f)
            yield f
            n += 1
            if n >= max_files:
                return


def _row_direct_datetime(row) -> tuple[pd.Timestamp, str]:
    """Essaie d'obtenir une date directement depuis le fichier DICOM de la ligne."""
    # 1) example_file est la source la plus directe et la plus rapide.
    example = row.get("example_file", None)
    if example is not None and not pd.isna(example):
        dt, src = _dicom_datetime_from_file(example)
        if pd.notna(dt):
            return dt, src + " via example_file"

    # 2) filepath peut être un fichier ou un dossier.
    filepath = row.get("filepath", None)
    modality = row.get("modality", None)
    if filepath is not None and not pd.isna(filepath):
        for f in _iter_candidate_files(filepath, modality=modality, max_files=30):
            dt, src = _dicom_datetime_from_file(f)
            if pd.notna(dt):
                return dt, src + " via scan fichier"

    # 3) Dernier recours : date incluse dans le nom du dossier.
    for col in ["filepath", "folder_name", "parent_folder", "series_desc"]:
        if col in row.index:
            dt, src = _date_from_path_text(row.get(col))
            if pd.notna(dt):
                return dt, src

    return pd.NaT, "date absente"


def _prepare_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise les dates CSV et enrichit CT/RT manquants par lecture DICOM directe."""
    out = df.copy()

    for col in ["first_file_datetime", "last_file_datetime"]:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
        else:
            out[col] = pd.NaT

    out["datetime_source"] = "CSV first/last_file_datetime"

    # Nettoyage critique : les dates 1900/1901 sont des sentinelles, pas des dates DICOM fiables.
    invalid_first = out["first_file_datetime"].notna() & ~out["first_file_datetime"].map(_is_valid_cohort_datetime)
    invalid_last = out["last_file_datetime"].notna() & ~out["last_file_datetime"].map(_is_valid_cohort_datetime)
    invalid_any = invalid_first | invalid_last
    out.loc[invalid_first, "first_file_datetime"] = pd.NaT
    out.loc[invalid_last, "last_file_datetime"] = pd.NaT
    out.loc[invalid_any, "datetime_source"] = "date CSV invalide ignorée"

    missing_date = out["first_file_datetime"].isna() & out["last_file_datetime"].isna()

    # On limite la relecture DICOM aux modalités qui posent problème sur la carte.
    mod_is_rt_ct = out.get("modality", pd.Series("", index=out.index)).astype(str).str.upper().isin(DATELESS_RT_CT_MODALITIES)
    to_repair = out[missing_date & mod_is_rt_ct].index.tolist()

    for idx in to_repair:
        dt, src = _row_direct_datetime(out.loc[idx])
        if pd.notna(dt):
            out.at[idx, "first_file_datetime"] = dt
            out.at[idx, "last_file_datetime"] = dt + pd.Timedelta(minutes=5)
            out.at[idx, "datetime_source"] = src
        else:
            out.at[idx, "datetime_source"] = src

    # Pour les autres modalités, on peut quand même utiliser la date du chemin si elle existe,
    # mais on ne crée jamais de date artificielle 1900/1901.
    remaining = out["first_file_datetime"].isna() & out["last_file_datetime"].isna()
    for idx in out[remaining].index.tolist():
        dt, src = _date_from_path_text(out.loc[idx].get("filepath", ""))
        if pd.notna(dt):
            out.at[idx, "first_file_datetime"] = dt
            out.at[idx, "last_file_datetime"] = dt + pd.Timedelta(minutes=5)
            out.at[idx, "datetime_source"] = src

    same = out["first_file_datetime"].notna() & (out["first_file_datetime"] == out["last_file_datetime"])
    out.loc[same, "last_file_datetime"] = out.loc[same, "first_file_datetime"] + pd.Timedelta(minutes=5)

    return out


def _prepare_plot_datetime(df: pd.DataFrame) -> pd.DataFrame:
    """Prépare une date d'affichage sans fallback artificiel.

    Règle :
    1. first_file_datetime ;
    2. last_file_datetime ;
    3. lecture DICOM directe déjà faite dans _prepare_dates ;
    4. date extraite du chemin si disponible ;
    5. sinon NaT : non affiché, mais listé dans un tableau.
    """
    tmp = _prepare_dates(df)

    tmp["plot_datetime"] = tmp["first_file_datetime"].fillna(tmp["last_file_datetime"])
    invalid_plot = tmp["plot_datetime"].notna() & ~tmp["plot_datetime"].map(_is_valid_cohort_datetime)
    tmp.loc[invalid_plot, "plot_datetime"] = pd.NaT
    tmp.loc[invalid_plot, "datetime_source"] = "date invalide ignorée"
    tmp["plot_date_missing"] = tmp["plot_datetime"].isna()
    tmp["plot_date_label"] = tmp["plot_datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    tmp.loc[tmp["plot_datetime"].isna(), "plot_date_label"] = "date absente"

    # Décalage léger seulement si vraie date connue, pour visualiser les points superposés.
    modality_offset_days = {
        "RTDOSE": -0.30,
        "RTPLAN": 0.0,
        "RTSTRUCT": 0.30,
    }
    for modality, offset in modality_offset_days.items():
        mask = (tmp["modality"].astype(str).str.upper() == modality) & tmp["plot_datetime"].notna()
        tmp.loc[mask, "plot_datetime"] = tmp.loc[mask, "plot_datetime"] + pd.to_timedelta(offset, unit="D")

    return tmp


def _get_timepoint_col(df: pd.DataFrame) -> str:
    if "timepoint_detected" in df.columns:
        return "timepoint_detected"
    if "timepoint" in df.columns:
        return "timepoint"
    return "timepoint_detected"


def _get_date_col(df: pd.DataFrame) -> str | None:
    for c in ["first_file_datetime", "study_date", "series_date", "acquisition_date", "dicom_date"]:
        if c in df.columns:
            return c
    return None


def build_rt_summary_table_html(df: pd.DataFrame) -> str:
    """Tableau patient × nombre de dossiers RTDOSE / RTSTRUCT / RTPLAN."""
    if df is None or df.empty or "modality" not in df.columns or "patient_id" not in df.columns:
        return "<div class='panel'><h2>Résumé RT par patient</h2><p>Aucune donnée RT disponible.</p></div>"

    tmp = df.copy()
    tmp["modality"] = tmp["modality"].astype(str).str.upper().str.strip()
    tmp["patient_num"] = tmp["patient_id"].map(_patient_num)

    rt = tmp[tmp["modality"].isin(RT_MODALITIES)].copy()
    if rt.empty:
        return "<div class='panel'><h2>Résumé RT par patient</h2><p>Aucun dossier RTDOSE / RTSTRUCT / RTPLAN détecté.</p></div>"

    rt_summary = (
        rt.groupby(["patient_id", "patient_num", "modality"], dropna=False)
        .size()
        .unstack(fill_value=0)
        .reset_index()
        .sort_values(["patient_num", "patient_id"])
    )

    for col in RT_MODALITIES:
        if col not in rt_summary.columns:
            rt_summary[col] = 0

    rt_summary["RT_COMPLET_DOSE_STRUCT"] = (rt_summary["RTDOSE"] > 0) & (rt_summary["RTSTRUCT"] > 0)
    rt_summary["RT_COMPLET_PLAN"] = rt_summary["RT_COMPLET_DOSE_STRUCT"] & (rt_summary["RTPLAN"] > 0)

    rows = []
    for _, r in rt_summary.iterrows():
        rows.append(
            "<tr>"
            f"<td>{_escape(r['patient_id'])}</td>"
            f"<td>{int(r['RTDOSE'])}</td>"
            f"<td>{int(r['RTSTRUCT'])}</td>"
            f"<td>{int(r['RTPLAN'])}</td>"
            f"<td>{'Oui' if bool(r['RT_COMPLET_DOSE_STRUCT']) else 'Non'}</td>"
            f"<td>{'Oui' if bool(r['RT_COMPLET_PLAN']) else 'Non'}</td>"
            "</tr>"
        )

    return f"""
    <div class='panel'>
    <h2>Résumé RT par patient</h2>
    <p class='subtitle'>Comptage folder-level des dossiers RT détectés.</p>
    <table class='summary-table compact'>
        <tr><th>Indicateur</th><th>N</th></tr>
        <tr><td>Patients avec au moins une donnée RT</td><td>{rt_summary['patient_id'].nunique()}</td></tr>
        <tr><td>Patients avec RTDOSE + RTSTRUCT</td><td>{int(rt_summary['RT_COMPLET_DOSE_STRUCT'].sum())}</td></tr>
        <tr><td>Patients avec RTDOSE + RTSTRUCT + RTPLAN</td><td>{int(rt_summary['RT_COMPLET_PLAN'].sum())}</td></tr>
    </table>
    <table class='summary-table'>
        <thead><tr><th>Patient</th><th>RTDOSE</th><th>RTSTRUCT</th><th>RTPLAN</th><th>RTDOSE + RTSTRUCT</th><th>RT complet + RTPLAN</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    </div>
    """


def build_patient_056_table_html(df: pd.DataFrame) -> str:
    """Bloc de vérification dédié au patient IC 056."""
    if df is None or df.empty or "patient_id" not in df.columns:
        return ""
    sub = df[df["patient_id"].astype(str).str.contains(r"\b056\b|IC\s*056|IC-056|IC_056", regex=True, na=False)].copy()
    if sub.empty:
        return "<div class='panel'><h2>Vérification patient IC 056</h2><p class='warn'>IC 056 non retrouvé dans la sortie file_discovery.</p></div>"

    sub = _prepare_dates(sub)
    date_col = _get_date_col(sub)
    tp_col = _get_timepoint_col(sub)
    display_cols = [c for c in ["patient_id", tp_col, "modality", date_col, "datetime_source", "series_desc", "n_files_in_series", "filepath"] if c and c in sub.columns]
    sub["_sort_tp"] = sub[tp_col].map(timepoint_sort_key) if tp_col in sub.columns else 999
    sub = sub.sort_values(["_sort_tp", "modality", "filepath"], na_position="last")

    rows = []
    for _, r in sub[display_cols].iterrows():
        rows.append("<tr>" + "".join(f"<td>{_escape(v)}</td>" for v in r.tolist()) + "</tr>")
    headers = "".join(f"<th>{html.escape(str(c))}</th>" for c in display_cols)
    return f"""
    <div class='panel'>
    <h2>Vérification patient IC 056</h2>
    <p class='subtitle'>Contrôle ciblé demandé : séries, modalités, dates et chemins détectés pour IC 056.</p>
    <table class='summary-table wide'><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table>
    </div>
    """


def build_date_inconsistency_table_html(df: pd.DataFrame) -> str:
    """Détecte les patients dont l'ordre des dates contredit l'ordre longitudinal des timepoints."""
    if df is None or df.empty or "patient_id" not in df.columns:
        return "<div class='panel'><h2>Dates incohérentes</h2><p>Aucune donnée disponible.</p></div>"

    tmp = _prepare_dates(df)
    tp_col = _get_timepoint_col(tmp)
    date_col = _get_date_col(tmp)
    if tp_col not in tmp.columns or date_col is None:
        return "<div class='panel'><h2>Dates incohérentes</h2><p>Colonnes timepoint/date absentes.</p></div>"

    tmp["date_value"] = pd.to_datetime(tmp[date_col], errors="coerce")
    tmp["tp_value"] = tmp[tp_col].fillna("UNKNOWN").astype(str)
    tmp["tp_rank"] = tmp["tp_value"].map(timepoint_sort_key)

    issue_rows = []
    for pid, sub in tmp.dropna(subset=["date_value"]).groupby("patient_id", dropna=False):
        tps = (
            sub.dropna(subset=["tp_rank"])
            .groupby(["tp_value", "tp_rank"], dropna=False)["date_value"]
            .min()
            .reset_index()
            .sort_values("tp_rank")
        )
        if len(tps) < 2:
            continue
        prev_date = None
        prev_tp = None
        inversions = []
        for r in tps.itertuples(index=False):
            cur_tp = r.tp_value
            cur_date = r.date_value
            if prev_date is not None and pd.notna(cur_date) and cur_date < prev_date:
                inversions.append(f"{prev_tp} ({prev_date.date()}) → {cur_tp} ({cur_date.date()})")
            prev_date = cur_date
            prev_tp = cur_tp
        if inversions:
            issue_rows.append({
                "patient_id": pid,
                "patient_num": _patient_num(pid),
                "issue": "Ordre temporel incohérent",
                "details": "; ".join(inversions),
            })

    if not issue_rows:
        return "<div class='panel'><h2>Dates incohérentes</h2><p class='ok'>Aucune incohérence temporelle détectée selon les timepoints disponibles.</p></div>"

    issues = pd.DataFrame(issue_rows).sort_values(["patient_num", "patient_id"])
    rows = []
    for _, r in issues.iterrows():
        rows.append(
            "<tr>"
            f"<td>{_escape(r['patient_id'])}</td>"
            f"<td>{_escape(r['issue'])}</td>"
            f"<td class='mono'>{_escape(r['details'])}</td>"
            "</tr>"
        )
    return f"""
    <div class='panel'>
    <h2>Patients avec dates incohérentes</h2>
    <p class='subtitle'>Un patient est signalé si un timepoint tardif possède une date plus ancienne qu'un timepoint plus précoce.</p>
    <table class='summary-table wide'>
        <thead><tr><th>Patient</th><th>Problème</th><th>Détails</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
    </table>
    </div>
    """


def build_missing_plot_dates_table_html(df_prepared: pd.DataFrame) -> str:
    """Liste les lignes non affichées car aucune vraie date n'a été trouvée."""
    if df_prepared is None or df_prepared.empty or "plot_datetime" not in df_prepared.columns:
        return ""
    missing = df_prepared[df_prepared["plot_datetime"].isna()].copy()
    if missing.empty:
        return "<div class='panel'><h2>Données non affichées faute de date</h2><p class='ok'>Toutes les lignes agrégées affichables possèdent une vraie date.</p></div>"

    missing["patient_num"] = missing["patient_id"].map(_patient_num)
    missing = missing.sort_values(["patient_num", "patient_id", "modality"], na_position="last")
    cols = [c for c in ["patient_id", "timepoint_detected", "modality", "n_files", "folder_examples", "path_examples", "datetime_source"] if c in missing.columns]

    rows = []
    for _, r in missing[cols].head(300).iterrows():
        rows.append("<tr>" + "".join(f"<td>{_escape(v)}</td>" for v in r.tolist()) + "</tr>")
    headers = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
    note = "" if len(missing) <= 300 else f"<p class='warn'>Affichage limité aux 300 premières lignes sur {len(missing)}.</p>"
    return f"""
    <div class='panel'>
    <h2>Données non affichées faute de date</h2>
    <p class='subtitle'>Ces lignes n'ont ni date CSV, ni date DICOM relue, ni date extraite du chemin. Aucune fausse date 1900/1901 n'est créée.</p>
    {note}
    <table class='summary-table wide'><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table>
    </div>
    """


def make_patient_interactive_map(df_patient: pd.DataFrame, patient_id: str, output_html: str | Path) -> dict:
    out = Path(output_html)
    ensure_parent(out)
    dfp = _prepare_plot_datetime(df_patient)
    dfp["sort_tp"] = dfp["timepoint_detected"].map(timepoint_sort_key) if "timepoint_detected" in dfp.columns else 999
    dfp = dfp.sort_values(["sort_tp", "modality", "folder_name"], na_position="last")
    dfp_plot = dfp[dfp["plot_datetime"].notna()].copy()

    fig = go.Figure()
    for mod, sub in dfp_plot.groupby("modality", dropna=False, sort=False):
        custom = []
        for _, r in sub.iterrows():
            custom.append([
                r.get("timepoint_detected", ""),
                r.get("n_files_in_series", ""),
                r.get("series_desc", ""),
                r.get("folder_name", ""),
                r.get("filepath", ""),
                r.get("plot_date_label", ""),
                r.get("datetime_source", ""),
            ])
        is_rtstruct = str(mod).upper() == "RTSTRUCT"
        fig.add_trace(go.Scatter(
            x=sub["plot_datetime"],
            y=sub["folder_name"],
            mode="markers",
            marker=dict(
                size=np.clip(np.log1p(sub["n_files_in_series"].fillna(1)) * 3, 7, 18) if not is_rtstruct else 10,
                color=MODALITY_COLORS.get(str(mod), "#7F8C8D"),
                symbol="diamond" if is_rtstruct else "circle",
                line=dict(color="black", width=1),
            ),
            name=str(mod),
            customdata=custom,
            hovertemplate=(
                "<b>%{y}</b><br>Timepoint: %{customdata[0]}<br>Fichiers: %{customdata[1]}"
                "<br>Date: %{customdata[5]}<br>Source date: %{customdata[6]}"
                "<br>SeriesDescription: %{customdata[2]}<br>Dossier: %{customdata[3]}"
                "<br>Chemin NAS: %{customdata[4]}<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=f"Carte patient folder-level — {patient_id}",
        height=max(700, 22 * max(len(dfp_plot), 1)),
        width=1500,
        template="plotly_white",
        xaxis_title="Date DICOM",
        yaxis_title="Dossiers séries",
        margin=dict(l=320, r=60, t=80, b=80),
    )
    graph = fig.to_html(full_html=False, include_plotlyjs="cdn")
    clinical_html = patient_clinical_table_html(dfp)
    html_page = (
        f"<!DOCTYPE html><html lang='fr'><head><meta charset='utf-8'><title>{html.escape(patient_id)}</title><style>{CSS}</style></head>"
        f"<body><h1>EpiBrainRad — {html.escape(patient_id)}</h1>{modality_legend_html()}"
        f"<div class='panel'><h2>Données DICOM disponibles</h2>{graph}</div>"
        f"<div class='panel'><h2>Tableau clinique patient</h2>{clinical_html}</div></body></html>"
    )
    out.write_text(html_page, encoding="utf-8")
    return {"patient_id": patient_id, "status": "OK", "html_path": str(out)}


def make_all_patient_maps(df: pd.DataFrame, out_dir: str | Path) -> pd.DataFrame:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    df_sorted = _sort_patients_frame(df)
    for pid, sub in df_sorted.groupby("patient_id", sort=False):
        try:
            rows.append(make_patient_interactive_map(sub, pid, out_dir / f"{pid.replace(' ', '_')}_file_discovery.html"))
        except Exception as exc:
            rows.append({"patient_id": pid, "status": "ERROR", "error": str(exc)})
    return pd.DataFrame(rows)


def _aggregate_global(df: pd.DataFrame) -> pd.DataFrame:
    dfg = _prepare_plot_datetime(df)
    grouped = []
    for keys, sub in dfg.groupby(["patient_id", "timepoint_detected", "modality"], dropna=False):
        pid, tp, mod = keys
        paths = sub["filepath"].dropna().astype(str).tolist() if "filepath" in sub.columns else []
        folders = sub["folder_name"].dropna().astype(str).head(4).tolist() if "folder_name" in sub.columns else []
        seqs = sorted(set([x for x in sub.get("mri_sequence", pd.Series(dtype=str)).dropna().astype(str) if x]))
        r0 = sub.iloc[0]

        plot_dates = sub["plot_datetime"].dropna()
        first_dates = sub["first_file_datetime"].dropna()
        last_dates = sub["last_file_datetime"].dropna()

        grouped.append({
            "patient_id": pid,
            "patient_num": _patient_num(pid),
            "timepoint_detected": tp,
            "modality": mod,
            "first_file_datetime": first_dates.min() if len(first_dates) else pd.NaT,
            "last_file_datetime": last_dates.max() if len(last_dates) else pd.NaT,
            "plot_datetime": plot_dates.min() if len(plot_dates) else pd.NaT,
            "plot_date_missing": len(plot_dates) == 0,
            "plot_date_label": plot_dates.min().strftime("%Y-%m-%d %H:%M:%S") if len(plot_dates) else "date absente",
            "datetime_source": " | ".join(sorted(set(sub.get("datetime_source", pd.Series(dtype=str)).dropna().astype(str).head(3)))) if "datetime_source" in sub.columns else "",
            "n_folders": len(sub),
            "n_files": int(sub["n_files_in_series"].sum()) if "n_files_in_series" in sub.columns else len(sub),
            "folder_examples": " | ".join(folders),
            "sequence_summary": ", ".join(seqs[:8]),
            "path_examples": "<br>".join(html.escape(p) for p in paths[:4]),
            "STATUT": r0.get("STATUT", ""),
            "AGE": r0.get("AGE", ""),
            "SEXE": r0.get("SEXE", ""),
            "CHIR": r0.get("CHIR", ""),
            "GRADE": r0.get("GRADE", ""),
            "has_csct_lb_m12": r0.get("has_csct_lb_m12", ""),
            "declin_1_5sd": r0.get("declin_1_5sd", ""),
        })

    out = pd.DataFrame(grouped)
    if out.empty:
        return out
    out["sort_tp"] = out["timepoint_detected"].map(timepoint_sort_key)
    return out.sort_values(["patient_num", "patient_id", "sort_tp", "modality"], na_position="last")


def dicom_global_summary_html(df: pd.DataFrame) -> str:
    dfg = df.copy()
    dfg["timepoint_detected"] = dfg["timepoint_detected"].fillna("UNKNOWN")
    rows = []
    for tp in TIMEPOINT_DISPLAY_ORDER:
        sub = dfg[dfg["timepoint_detected"] == tp]
        if sub.empty:
            continue
        rows.append(f"<tr><td>{tp}</td><td>{sub['patient_id'].nunique()}</td><td>{len(sub)}</td><td>{int(sub['n_files_in_series'].sum())}</td></tr>")
    return "<div class='panel'><h2>Résumé global DICOM par timepoint</h2><table class='summary-table compact'><tr><th>Timepoint</th><th>Patients</th><th>Dossiers</th><th>Fichiers</th></tr>" + "".join(rows) + "</table></div>"


def _patient_order_js(patient_order: list[str]) -> str:
    fixed_order_js = json.dumps(patient_order)
    return f"""
<script>
(function() {{
    const fixedOrder = {fixed_order_js};
    function enforcePatientOrder() {{
        const graphs = document.getElementsByClassName("plotly-graph-div");
        for (const gd of graphs) {{
            if (gd && gd.layout && gd.layout.yaxis) {{
                Plotly.relayout(gd, {{
                    "yaxis.categoryorder": "array",
                    "yaxis.categoryarray": fixedOrder,
                    "yaxis.autorange": false
                }});
            }}
        }}
    }}
    window.addEventListener("load", function() {{ setTimeout(enforcePatientOrder, 200); }});
    document.addEventListener("click", function() {{ setTimeout(enforcePatientOrder, 250); }});
}})();
</script>
"""


def make_global_interactive_map(df: pd.DataFrame, out_html: str | Path) -> dict:
    out = Path(out_html)
    ensure_parent(out)

    dfg_all = _aggregate_global(df)
    patient_order = _sorted_patient_order_desc(df)
    dfg_plot = dfg_all[dfg_all["plot_datetime"].notna()].copy() if not dfg_all.empty else dfg_all

    fig = go.Figure()
    modality_priority = {"MR": 0, "CT": 1, "RTDOSE": 2, "RTPLAN": 3, "RTSTRUCT": 4, "REG": 5, "PT": 6, "OT": 7, "SEG": 8, "SR": 9, "UNKNOWN": 10}
    mods = sorted(dfg_plot["modality"].dropna().astype(str).unique(), key=lambda m: modality_priority.get(m, 99)) if not dfg_plot.empty else []

    for mod in mods:
        sub = dfg_plot[dfg_plot["modality"].astype(str) == mod].copy()
        if sub.empty:
            continue
        custom = []
        for _, r in sub.iterrows():
            custom.append([
                r.timepoint_detected,
                r.n_folders,
                r.n_files,
                r.sequence_summary,
                r.folder_examples,
                r.path_examples,
                r.STATUT,
                r.AGE,
                r.SEXE,
                r.CHIR,
                r.GRADE,
                r.plot_date_label,
                r.datetime_source,
            ])

        is_rtstruct = str(mod).upper() == "RTSTRUCT"
        marker_size = np.clip(np.log1p(sub["n_files"]) * 2.2, 6, 16)
        if is_rtstruct:
            marker_size = np.repeat(10.0, len(sub))

        fig.add_trace(go.Scatter(
            x=sub["plot_datetime"],
            y=sub["patient_id"],
            mode="markers",
            marker=dict(
                size=marker_size,
                color=MODALITY_COLORS.get(str(mod), "#7F8C8D"),
                symbol="diamond" if is_rtstruct else "circle",
                line=dict(color="black", width=1),
            ),
            name=str(mod),
            customdata=custom,
            hovertemplate=(
                "<b>%{y}</b><br>Timepoint: %{customdata[0]}<br>Modalité: " + str(mod) +
                "<br>Dossiers: %{customdata[1]}<br>Fichiers: %{customdata[2]}"
                "<br>Date: %{customdata[11]}<br>Source date: %{customdata[12]}"
                "<br>Séquences: %{customdata[3]}<br>Dossiers exemples: %{customdata[4]}"
                "<br><b>Chemins NAS exemples</b>:<br>%{customdata[5]}"
                "<br>Clinique: statut=%{customdata[6]}, âge=%{customdata[7]}, sexe=%{customdata[8]}, CHIR=%{customdata[9]}, grade=%{customdata[10]}<extra></extra>"
            ),
        ))

    fig.update_layout(
        title="EpiBrainRad — Carte globale longitudinale de la cohorte",
        height=max(900, 24 * len(patient_order)) if patient_order else 900,
        width=1650,
        template="plotly_white",
        xaxis_title="Date DICOM",
        yaxis=dict(
            title="Patient",
            type="category",
            categoryorder="array",
            categoryarray=patient_order,
            autorange=False,
        ),
        hovermode="closest",
        margin=dict(l=120, r=60, t=90, b=80),
    )

    graph = fig.to_html(full_html=False, include_plotlyjs="cdn") + _patient_order_js(patient_order)

    html_page = f"""<!DOCTYPE html>
<html lang='fr'>
<head><meta charset='utf-8'><title>EpiBrainRad — Carte globale cohorte</title><style>{CSS}</style></head>
<body>
<h1>EpiBrainRad — Carte globale longitudinale de la cohorte</h1>
<div class='subtitle'>Chaque point = agrégation patient / timepoint / modalité. Les dates CT/RT manquantes ou invalides dans le CSV sont relues depuis les métadonnées DICOM, puis depuis le nom de dossier si nécessaire. Les dates sentinelles 1900/1901 sont ignorées.</div>
{modality_legend_html()}
<div class='panel'><h2>Carte globale des données disponibles</h2>{graph}</div>
{build_rt_summary_table_html(df)}
{cohort_clinical_summary_html(df)}
{dicom_global_summary_html(df)}
</body></html>"""

    out.write_text(html_page, encoding="utf-8")
    return {"status": "OK", "html_path": str(out), "n_patients": int(dfg_plot["patient_id"].nunique()) if not dfg_plot.empty else 0}
