# -*- coding: utf-8 -*-
"""clinical_utils.py — EpiBrainRad

Version fonctionnelle pour le fichier clinique anonymisé multi-feuilles :
Base_donnée_EPIBRAIN_A_JOUR_042026_LATEST.xlsx
"""
from __future__ import annotations

from pathlib import Path
import html
import re
import unicodedata
import pandas as pd


def normalize_patient_id(value):
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    s = str(value).strip().upper().replace("IC-", "IC ").replace("IC_", "IC ")
    m = re.search(r"(\d{1,3})", s)
    return f"IC {int(m.group(1)):03d}" if m else (s or None)


def _norm_col(value) -> str:
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().strip().replace("≥", "gte").replace(">=", "gte")
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def _is_missing(value) -> bool:
    try:
        miss = pd.isna(value)
        if isinstance(miss, bool) and miss:
            return True
    except Exception:
        pass
    s = str(value).strip()
    return s == "" or s.lower() in {"nan", "nat", "none", "null", "na", "n/a", "<na>"}


def _safe(value) -> str:
    return "" if _is_missing(value) else html.escape(str(value))


def _find_col(df: pd.DataFrame, candidates) -> str | None:
    if df is None or df.empty:
        return None
    norm_to_original = {_norm_col(c): c for c in df.columns}
    cands = [_norm_col(c) for c in candidates]
    for c in cands:
        if c in norm_to_original:
            return norm_to_original[c]
    for original in df.columns:
        on = _norm_col(original)
        for c in cands:
            if len(c) >= 3 and (c in on or on in c):
                return original
    return None


def _id_series(df: pd.DataFrame, candidates) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=object)
    norm_to_original = {_norm_col(c): c for c in df.columns}
    col = None
    for cand in candidates:
        n = _norm_col(cand)
        if n in norm_to_original:
            col = norm_to_original[n]
            break
    if col is None and len(df.columns) > 0:
        first = df.columns[0]
        vals = pd.to_numeric(df[first], errors="coerce")
        if vals.notna().sum() > 0:
            col = first
    if col is None:
        return pd.Series([None] * len(df), index=df.index)
    return df[col].map(normalize_patient_id)


def _format_value(value) -> str:
    if _is_missing(value):
        return "Non disponible"
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    try:
        x = float(value)
        if pd.notna(x):
            return str(int(x)) if x.is_integer() else f"{x:.2f}"
    except Exception:
        pass
    return _safe(value)


def _format_score(value) -> str:
    if _is_missing(value):
        return "Non disponible"
    try:
        x = float(value)
        if pd.notna(x):
            return f"{x:.2f}"
    except Exception:
        pass
    return _safe(value)


def _format_date(value) -> str:
    if _is_missing(value):
        return "Non disponible"
    dt = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return dt.strftime("%Y-%m-%d") if pd.notna(dt) else _safe(value)


def _format_bool(value) -> str:
    if _is_missing(value):
        return "Non disponible"
    if isinstance(value, str):
        return "Oui" if value.strip().lower() in {"oui", "true", "vrai", "1", "yes", "y"} else "Non"
    return "Oui" if bool(value) else "Non"


def _read_sheet(xls: pd.ExcelFile, preferred_name: str) -> pd.DataFrame:
    key = _norm_col(preferred_name)
    by_norm = {_norm_col(n): n for n in xls.sheet_names}
    if key in by_norm:
        return pd.read_excel(xls, sheet_name=by_norm[key])
    for n in xls.sheet_names:
        nn = _norm_col(n)
        if key in nn or nn in key:
            return pd.read_excel(xls, sheet_name=n)
    return pd.DataFrame()


def _extract_crf_patient(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["patient_id"])
    out = pd.DataFrame(index=df.index)
    out["patient_id"] = _id_series(df, ["ID_PATIENT", "CODE_EBR", "Code EBR", "ID", "patient_id", "patient"])
    mapping = {
        "AGE": ["AGE", "âge", "age"],
        "SEXE": ["SEXE", "sex", "sexe", "genre"],
        "GRADE": ["GRADE", "grade", "grade oms", "grade who"],
        "CHIR": ["CHIR", "chirurgie", "surgery"],
        "DATE_CHIR": ["DATE-CHIR", "DATE_CHIR", "date chir", "date chirurgie"],
    }
    for out_col, aliases in mapping.items():
        src = _find_col(df, aliases)
        out[out_col] = df[src] if src is not None else pd.NA
    out["AGE"] = pd.to_numeric(out["AGE"], errors="coerce")
    out["DATE_CHIR"] = pd.to_datetime(out["DATE_CHIR"], errors="coerce", dayfirst=True)
    has_info = out[["AGE", "SEXE", "GRADE", "CHIR", "DATE_CHIR"]].notna().any(axis=1)
    return out.loc[has_info].dropna(subset=["patient_id"]).drop_duplicates("patient_id", keep="first")


def _extract_suivi_ebr(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["patient_id"])
    out = pd.DataFrame(index=df.index)
    out["patient_id"] = _id_series(df, ["ID_PATIENT", "CODE_EBR", "Code EBR", "ID", "patient_id", "patient"])
    mapping = {
        "STATUT": ["STATUT", "status", "statut patient"],
        "FIN_SUIVI": ["FIN SUIVI", "FIN_SUIVI", "date fin suivi", "fin suivi"],
        "PDS_LB": ["PDS_LB", "pds lb", "date pds lb", "baseline"],
        "KPS": ["KPS", "karnofsky"],
        "PDS_RT12_REEL": ["PDS_RT12_REEL", "pds rt12 reel", "pds rt12 réel", "rt12"],
        "PDS_RT24_REEL": ["PDS_RT24_REEL", "pds rt24 reel", "pds rt24 réel", "rt24"],
    }
    for out_col, aliases in mapping.items():
        src = _find_col(df, aliases)
        out[out_col] = df[src] if src is not None else pd.NA
    for c in ["FIN_SUIVI", "PDS_LB", "PDS_RT12_REEL", "PDS_RT24_REEL"]:
        out[c] = pd.to_datetime(out[c], errors="coerce", dayfirst=True)
    out["KPS"] = pd.to_numeric(out["KPS"], errors="coerce")
    return out.dropna(subset=["patient_id"]).drop_duplicates("patient_id", keep="first")


def _extract_crf_csct(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["patient_id"])
    out = pd.DataFrame(index=df.index)
    out["patient_id"] = _id_series(df, ["CODE_EBR", "Code EBR", "ID_PATIENT", "ID", "patient_id", "patient"])
    mapping = {
        "DATE_LB_CSCT": ["Date LB", "date lb", "date baseline", "date lb csct"],
        "LB_ZS": ["LB_ZS", "lb zs", "zs lb", "zscore lb", "z score lb"],
        "M12_ZS": ["M12_ZS", "m12 zs", "zs m12", "zscore m12", "z score m12"],
    }
    for out_col, aliases in mapping.items():
        src = _find_col(df, aliases)
        out[out_col] = df[src] if src is not None else pd.NA
    out["DATE_LB_CSCT"] = pd.to_datetime(out["DATE_LB_CSCT"], errors="coerce", dayfirst=True)
    out["LB_ZS"] = pd.to_numeric(out["LB_ZS"], errors="coerce")
    out["M12_ZS"] = pd.to_numeric(out["M12_ZS"], errors="coerce")
    out["DELTA_ZS_M12_MINUS_LB"] = out["M12_ZS"] - out["LB_ZS"]
    out["DELTA_ZS_LE_MINUS_1_5"] = out["DELTA_ZS_M12_MINUS_LB"] <= -1.5
    out["has_csct_lb_m12"] = out["LB_ZS"].notna() & out["M12_ZS"].notna()
    out["declin_1_5sd"] = out["DELTA_ZS_LE_MINUS_1_5"].fillna(False).astype(bool)
    return out.dropna(subset=["patient_id"]).drop_duplicates("patient_id", keep="first")


def _extract_crf_rt(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["patient_id"])
    out = pd.DataFrame(index=df.index)
    out["patient_id"] = _id_series(df, ["CODE_EBR", "Code EBR", "ID_PATIENT", "ID", "patient_id", "patient"])
    src = _find_col(df, ["TECHNIQUE", "technique rt", "technique radiotherapie", "technique radiothérapie"])
    out["TECHNIQUE"] = df[src] if src is not None else pd.NA
    return out.dropna(subset=["patient_id"]).drop_duplicates("patient_id", keep="first")


def build_clinical_patient_table(source) -> pd.DataFrame:
    if source is None:
        return pd.DataFrame()
    if isinstance(source, (str, Path)):
        xls = pd.ExcelFile(Path(source))
        parts = [
            _extract_crf_patient(_read_sheet(xls, "CRF_PATIENT")),
            _extract_suivi_ebr(_read_sheet(xls, "SUIVI_EBR")),
            _extract_crf_csct(_read_sheet(xls, "CRF_ CSCT")),
            _extract_crf_rt(_read_sheet(xls, "CRF_RT")),
        ]
        ids = pd.concat([p.get("patient_id", pd.Series(dtype=object)) for p in parts], ignore_index=True).dropna().drop_duplicates()
        out = pd.DataFrame({"patient_id": sorted(ids.tolist())})
        for p in parts:
            if p is not None and not p.empty and "patient_id" in p.columns:
                out = out.merge(p, on="patient_id", how="left")
        return _finalize_clinical_table(out)
    if isinstance(source, pd.DataFrame):
        df = source.copy()
        id_col = _find_col(df, ["ID_PATIENT", "CODE_EBR", "Code EBR", "patient_id", "patient", "ID"])
        df["patient_id"] = df[id_col].map(normalize_patient_id) if id_col is not None else None
        return _finalize_clinical_table(df.dropna(subset=["patient_id"]))
    return pd.DataFrame()


def _finalize_clinical_table(out: pd.DataFrame) -> pd.DataFrame:
    # EpiBrainRad/RADIO-AIDE contient 99 patients anonymisés IC 001 à IC 099.
    # Certaines feuilles du classeur contiennent des lignes supplémentaires (>99)
    # qui ne doivent pas entrer dans les cartes de cette cohorte.
    if "patient_id" in out.columns:
        patient_num = out["patient_id"].astype(str).str.extract(r"(\d{1,3})", expand=False)
        patient_num = pd.to_numeric(patient_num, errors="coerce")
        out = out.loc[patient_num.between(1, 99, inclusive="both")].copy()

    for col in ["AGE", "SEXE", "STATUT", "GRADE", "CHIR", "DATE_CHIR", "PDS_LB", "FIN_SUIVI", "DATE_LB_CSCT", "LB_ZS", "M12_ZS", "TECHNIQUE"]:
        if col not in out.columns:
            out[col] = pd.NA
    out["LB_ZS"] = pd.to_numeric(out["LB_ZS"], errors="coerce")
    out["M12_ZS"] = pd.to_numeric(out["M12_ZS"], errors="coerce")
    out["DELTA_ZS_M12_MINUS_LB"] = out["M12_ZS"] - out["LB_ZS"]
    out["DELTA_ZS_LE_MINUS_1_5"] = out["DELTA_ZS_M12_MINUS_LB"] <= -1.5
    out["declin_1_5sd"] = out["DELTA_ZS_LE_MINUS_1_5"].fillna(False).astype(bool)
    out["has_csct_lb_m12"] = out["LB_ZS"].notna() & out["M12_ZS"].notna()
    return out.drop_duplicates("patient_id", keep="first")


def load_clinical_dataset(xlsx_path) -> pd.DataFrame:
    path = Path(xlsx_path)
    if not path.exists():
        print(f"[WARNING] Fichier clinique introuvable : {path}")
        return pd.DataFrame()
    try:
        print(f"\nChargement clinique : {path}")
        out = build_clinical_patient_table(path)
        n = out["patient_id"].nunique() if "patient_id" in out.columns else len(out)
        print(f"Patients cliniques chargés : {n}")
        return out
    except Exception as exc:
        print(f"[ERROR] Impossible de charger le dataset clinique : {path}\n{exc}")
        return pd.DataFrame()


def merge_clinical(df_discovery: pd.DataFrame, df_clinical: pd.DataFrame) -> pd.DataFrame:
    if df_discovery is None or df_discovery.empty:
        return df_discovery
    if df_clinical is None or df_clinical.empty or "patient_id" not in df_clinical.columns:
        return df_discovery
    clinical_cols = [c for c in df_clinical.columns if c != "patient_id"]
    base = df_discovery.drop(columns=[c for c in clinical_cols if c in df_discovery.columns], errors="ignore")
    return base.merge(df_clinical, on="patient_id", how="left")


def patient_clinical_table_html(df_patient: pd.DataFrame) -> str:
    if df_patient is None or df_patient.empty:
        return "<p>Aucune donnée clinique fusionnée.</p>"
    r = df_patient.iloc[0]

    def getv(col):
        return r.get(col, pd.NA)

    def row(label, col=None, value=None, formatter=_format_value):
        v = getv(col) if col is not None else value
        return f"<tr><th>{html.escape(str(label))}</th><td>{formatter(v)}</td></tr>"

    lb = pd.to_numeric(pd.Series([getv("LB_ZS")]), errors="coerce").iloc[0]
    m12 = pd.to_numeric(pd.Series([getv("M12_ZS")]), errors="coerce").iloc[0]
    if pd.notna(lb) and pd.notna(m12):
        delta = float(m12) - float(lb)
        delta_ge_1_5 = delta <= -1.5
    else:
        delta = pd.NA
        delta_ge_1_5 = pd.NA

    crf_patient_rows = "".join([
        row("Patient", "patient_id"),
        row("Âge", "AGE"),
        row("Sexe", "SEXE"),
        row("Statut", "STATUT"),
        row("Grade", "GRADE"),
        row("CHIR", "CHIR"),
        row("Date CHIR", "DATE_CHIR", formatter=_format_date),
    ])
    suivi_rows = "".join([
        row("PDS_LB", "PDS_LB", formatter=_format_date),
        row("FIN SUIVI", "FIN_SUIVI", formatter=_format_date),
    ])
    csct_rows = "".join([
        row("Date LB CSCT", "DATE_LB_CSCT", formatter=_format_date),
        row("LB_ZS", "LB_ZS", formatter=_format_score),
        row("M12_ZS", "M12_ZS", formatter=_format_score),
        row("Delta M12_ZS − LB_ZS", value=delta, formatter=_format_score),
        row("Déclin ≤ -1,5", value=delta_ge_1_5, formatter=_format_bool),
    ])
    return f"""
    <h3>CRF Patient</h3>
    <table class='summary-table compact'>{crf_patient_rows}</table>
    <h3>Suivi_EBR</h3>
    <table class='summary-table compact'>{suivi_rows}</table>
    <h3>CRF_CSCT</h3>
    <table class='summary-table compact'>{csct_rows}</table>
    """


def _counts_table(series: pd.Series, title: str) -> str:
    if series is None or series.dropna().empty:
        return ""
    s = series.dropna().astype(str).str.strip()
    s = s[s.ne("")]
    if s.empty:
        return ""
    vc = s.value_counts(dropna=False)
    rows = "".join(f"<tr><td>{html.escape(str(k))}</td><td>{int(v)}</td></tr>" for k, v in vc.items())
    return f"<h3>{html.escape(title)}</h3><table class='summary-table compact'><tr><th>Valeur</th><th>N</th></tr>{rows}</table>"


def cohort_clinical_summary_html(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "<div class='panel'><h2>Résumé clinique global</h2><p>Aucune donnée clinique disponible.</p></div>"
    clin = df.drop_duplicates("patient_id") if "patient_id" in df.columns else df.copy()
    n = clin["patient_id"].nunique() if "patient_id" in clin.columns else len(clin)
    has_csct = int(clin.get("has_csct_lb_m12", pd.Series(False, index=clin.index)).fillna(False).astype(bool).sum())
    declin = int(clin.get("declin_1_5sd", pd.Series(False, index=clin.index)).fillna(False).astype(bool).sum())
    age = pd.to_numeric(clin.get("AGE", pd.Series(dtype=float)), errors="coerce")
    age_txt = "NA" if not age.notna().any() else f"n={int(age.notna().sum())}, min={int(age.min())}, max={int(age.max())}, moyenne={age.mean():.1f}"
    parts = ["<div class='panel'><h2>Résumé clinique global</h2>"]
    parts.append(
        "<table class='summary-table compact'><tr><th>Indicateur</th><th>Valeur</th></tr>"
        f"<tr><td>Patients cliniques</td><td>{n}</td></tr>"
        f"<tr><td>Patients avec CSCT LB + M12</td><td>{has_csct}</td></tr>"
        f"<tr><td>Patients avec déclin M12_ZS − LB_ZS ≤ -1,5</td><td>{declin}</td></tr>"
        f"<tr><td>Âge</td><td>{html.escape(age_txt)}</td></tr></table>"
    )
    for col, title in [("STATUT", "Statuts possibles"), ("SEXE", "Sexe"), ("CHIR", "CHIR"), ("GRADE", "Grade"), ("TECHNIQUE", "Techniques dans l'étude")]:
        if col in clin.columns:
            block = _counts_table(clin[col], title)
            if block:
                parts.append(block)
    parts.append("</div>")
    return "\n".join(parts)
