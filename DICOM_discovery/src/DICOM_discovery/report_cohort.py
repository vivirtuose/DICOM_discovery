"""Self-contained cohort report — RT integrity + cohort timeline map in one HTML.

This module is a pure *consumer* of the existing analysis outputs
(:func:`DICOM_discovery.rt_integrity.build_rt_rollup` / ``build_rt_integrity``). It assembles
them into a single file that opens by double-click on an air-gapped clinical workstation:
Plotly is embedded (no CDN) exactly as :mod:`DICOM_discovery.report_map` does, and all table
interactivity (sort / filter / CSV export / per-patient drill-down) is vanilla JavaScript
inlined into the page. Nothing is fetched at view time.

Design language: a sober clinical *document* — the look of hospital / clinical-trial data
software, not a dashboard. A quiet grey paper surface, one restrained slate-blue accent, and
colour spent only where it carries meaning (the four verdicts, present/absent in the data
strip). The RT table reads as a work queue: one row per patient with verdict + cause + next
action, WARN/INCOMPLETE ordered first, each verdict tag legible without colour (per-status
class + word + aria-label). A compact data-presence strip (CT·STR·PLAN·DOSE · GTV·CTV·PTV)
shows at a glance which pieces of each patient's RT record exist.

**Research Use Only — not a medical device.**

The data-assembly layer (KPI counts, table-row dicts) is kept in small, testable functions
separate from the HTML string templating, so the numbers can be asserted without parsing
HTML.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .completeness import Protocol, patient_completeness
from .rt_integrity import order_for_review

try:
    import plotly.graph_objects as go

    _HAS_PLOTLY = True
except Exception:  # pragma: no cover
    _HAS_PLOTLY = False

LOG = logging.getLogger("DICOM_discovery.report_cohort")

RUO_TEXT = "Research Use Only — not a medical device."

# Verdict colours (semantics fixed by the design spec). Muted, document-grade tones —
# saturated enough to carry meaning, never bright enough to read as a dashboard. NO_RT is a
# neutral slate: a patient with no RT objects is *out of scope*, not a failure.
VERDICT_COLORS = {
    "OK": "#2e7d4f",          # muted green
    "WARN": "#946000",        # ochre
    "INCOMPLETE": "#b42318",  # brick red
    "NO_RT": "#5b6775",       # slate (out of scope)
}
VERDICT_ORDER = ["OK", "WARN", "INCOMPLETE", "NO_RT"]

# Per-modality colours for the cohort timeline map (one legend entry per modality).
_MOD_COLORS = {
    "MR": "#4878a8", "CT": "#5a9367", "RTSTRUCT": "#c0883b", "RTPLAN": "#9a6fb0",
    "RTDOSE": "#b5564f", "PT": "#4aa3a3", "REG": "#8a8f99", "SEG": "#7d6f4f",
    "SR": "#a0a0a0", "OT": "#888888",
}
_MOD_ORDER = {"MR": 0, "CT": 1, "RTSTRUCT": 2, "RTPLAN": 3, "RTDOSE": 4,
              "PT": 5, "REG": 6, "SEG": 7, "SR": 8, "OT": 9}


def _pnum(pid: str) -> float:
    import re
    m = re.search(r"(\d+)", str(pid))
    return float(m.group(1)) if m else float("inf")


# RT chain glyph order — the signature element: a compact CT→STRUCT→PLAN→DOSE strip that
# shows which links of the chain a patient actually has.
_CHAIN = [
    ("has_CT", "CT"),
    ("has_RTSTRUCT", "STR"),
    ("has_RTPLAN", "PLAN"),
    ("has_RTDOSE", "DOSE"),
]


# --------------------------------------------------------------------------- #
# Data assembly (testable; no HTML)
# --------------------------------------------------------------------------- #
def verdict_counts(rollup_df: pd.DataFrame) -> Dict[str, int]:
    """Count patients per RT verdict, always returning all four keys (0 if absent)."""
    counts = {k: 0 for k in VERDICT_ORDER}
    if rollup_df is None or rollup_df.empty or "rt_status" not in rollup_df:
        return counts
    for status, n in rollup_df["rt_status"].value_counts().items():
        counts[str(status)] = counts.get(str(status), 0) + int(n)
    return counts


def cohort_pct_complete(comp_long: pd.DataFrame) -> Optional[float]:
    """Mean per-patient completeness % over mappable patients, or None if none mappable."""
    if comp_long is None:
        return None
    summary = patient_completeness(comp_long)
    if summary.empty:
        return None
    return round(float(summary["pct_complete"].mean()), 1)


def build_kpis(rollup_df: pd.DataFrame, comp_long: pd.DataFrame) -> Dict[str, object]:
    """Assemble the KPI block: verdict counts, total patients, cohort % complete."""
    counts = verdict_counts(rollup_df)
    n_patients = int(rollup_df.shape[0]) if rollup_df is not None and not rollup_df.empty else 0
    return {
        "n_patients": n_patients,
        "verdicts": counts,
        "pct_complete": cohort_pct_complete(comp_long),
    }


def rollup_rows(rollup_df: pd.DataFrame) -> List[dict]:
    """One dict per patient for the RT-integrity table (presentation-shaped)."""
    if rollup_df is None or rollup_df.empty:
        return []
    rows: List[dict] = []
    for r in rollup_df.to_dict("records"):
        rows.append({
            "patient_id": str(r.get("patient_id", "")),
            "rt_status": str(r.get("rt_status", "")),
            "n_studies": int(r.get("n_studies", 0) or 0),
            "n_rt_studies": int(r.get("n_rt_studies", 0) or 0),
            "fragmented": bool(r.get("fragmented", False)),
            "chain": [bool(r.get(key, False)) for key, _ in _CHAIN],
            "targets": {t: bool(r.get(f"roi_{t}", False)) for t in ("GTV", "CTV", "PTV")},
            "reason": str(r.get("reason", "") or ""),
            "action": str(r.get("action", "") or ""),
            "n_roi_nonstandard": int(r.get("n_roi_nonstandard", 0) or 0),
        })
    return rows


def study_findings(study_df: pd.DataFrame) -> Dict[str, List[dict]]:
    """Map patient_id -> list of per-study finding records for the drill-down panel."""
    out: Dict[str, List[dict]] = {}
    if study_df is None or study_df.empty:
        return out
    for r in study_df.to_dict("records"):
        pid = str(r.get("patient_id", ""))
        findings_raw = str(r.get("findings", "") or "")
        parsed: List[dict] = []
        for chunk in (c.strip() for c in findings_raw.split(" ; ") if c.strip()):
            # Format: "[SEVERITY/CONFIDENCE] CODE: message"
            severity = confidence = ""
            text = chunk
            if chunk.startswith("[") and "]" in chunk:
                tag, text = chunk[1:].split("]", 1)
                text = text.strip()
                if "/" in tag:
                    severity, confidence = (p.strip() for p in tag.split("/", 1))
            parsed.append({"severity": severity, "confidence": confidence, "text": text})
        out.setdefault(pid, []).append({
            "study_date": str(r.get("study_date", "") or ""),
            "rt_status": str(r.get("rt_status", "") or ""),
            "n_roi": int(r.get("n_roi", 0) or 0),
            "findings": parsed,
        })
    return out


def completeness_rows(comp_long: pd.DataFrame) -> List[dict]:
    """Per-patient completeness rows for the completeness-tab table."""
    summary = patient_completeness(comp_long)
    if summary.empty:
        return []
    return [{
        "patient": str(r["patient"]),
        "n_expected": int(r["n_expected"]),
        "n_present": int(r["n_present"]),
        "n_missing": int(r["n_missing"]),
        "pct_complete": float(r["pct_complete"]),
    } for _, r in summary.iterrows()]


# --------------------------------------------------------------------------- #
# Heatmap figure (embedded, no CDN) — reuses report_map's colour encoding
# --------------------------------------------------------------------------- #
def _timeline_map_html(table: pd.DataFrame, embed_js: bool = True) -> str:
    """Interactive cohort timeline (reintegrated from file_discovery): one marker per
    (patient, study date, modality), coloured by modality. Every point is **legended**
    (modality, via the Plotly legend) and **sourced** on hover — the tooltip carries the
    DICOM source path(s) so a verdict can be traced back to the file it came from.

    Built straight from the canonical table; Plotly is embedded inline (air-gapped).
    """
    placeholder = ("<p class='note'>Cohort map unavailable "
                   "(plotly not installed or no dated studies).</p>")
    if not _HAS_PLOTLY or table is None or getattr(table, "empty", True):
        return placeholder
    df = table[table["modality"].notna()].copy()
    df["_date"] = pd.to_datetime(df["study_date"].astype(str), format="%Y%m%d", errors="coerce")
    df = df[df["_date"].notna()]
    if df.empty:
        return placeholder

    agg = (df.groupby(["patient_id", "_date", "modality"])
             .agg(n_series=("series_uid", "nunique"),
                  n_studies=("study_uid", "nunique"),
                  paths=("path", lambda s: list(dict.fromkeys(s))[:4]))
             .reset_index())
    patients = sorted(agg["patient_id"].astype(str).unique(), key=lambda p: (_pnum(p), p))

    fig = go.Figure()
    for mod in sorted(agg["modality"].astype(str).unique(), key=lambda m: _MOD_ORDER.get(m, 99)):
        sub = agg[agg["modality"].astype(str) == mod]
        custom = []
        for _, r in sub.iterrows():
            src = "<br>".join("• " + _esc(p) for p in r["paths"])
            custom.append([mod, int(r["n_series"]), int(r["n_studies"]),
                           r["_date"].strftime("%Y-%m-%d"), src])
        is_struct = mod.upper() == "RTSTRUCT"
        size = 11 if is_struct else np.clip(np.log1p(sub["n_series"].to_numpy()) * 4 + 6, 6, 16)
        fig.add_trace(go.Scatter(
            x=sub["_date"], y=sub["patient_id"].astype(str), mode="markers", name=mod,
            marker=dict(size=size, color=_MOD_COLORS.get(mod, "#7f8c8d"),
                        symbol="diamond" if is_struct else "circle",
                        line=dict(color="rgba(20,30,45,.5)", width=1)),
            customdata=custom,
            hovertemplate=("<b>%{y}</b> · %{customdata[0]}"
                           "<br>Date : %{customdata[3]}"
                           "<br>Series : %{customdata[1]} · Studies : %{customdata[2]}"
                           "<br><b>Source</b> :<br>%{customdata[4]}<extra></extra>"),
        ))
    fig.update_layout(
        template="plotly_white",
        height=max(300, 26 * len(patients) + 150),
        margin=dict(t=24, l=96, r=24, b=56),
        xaxis=dict(title="DICOM study date", gridcolor="#eef1f5"),
        yaxis=dict(title="patient", type="category", categoryorder="array",
                   categoryarray=patients, autorange="reversed", gridcolor="#eef1f5"),
        legend=dict(orientation="h", y=1.04, x=0, title=""),
        font=dict(color="#1b2430", family="ui-sans-serif, system-ui, sans-serif", size=12),
        hovermode="closest", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig.to_html(full_html=False, include_plotlyjs=bool(embed_js))


# --------------------------------------------------------------------------- #
# HTML templating
# --------------------------------------------------------------------------- #
def _esc(v) -> str:
    return html.escape(str(v))


# The data-presence strip: the report's one signature element. A single compact row of
# seven cells — the RT chain (CT · STR · PLAN · DOSE) followed by the target volumes
# (GTV · CTV · PTV) — that shows at a glance which pieces of a patient's RT record exist.
# Present cells are filled and ticked; absent cells are dashed and dimmed. It reads like a
# checklist on a clinical worklist, not a progress bar.
_PRESENCE = [
    ("CT", "CT image"),
    ("STR", "RT structure set"),
    ("PLAN", "RT plan"),
    ("DOSE", "RT dose"),
    ("GTV", "GTV contour"),
    ("CTV", "CTV contour"),
    ("PTV", "PTV contour"),
]


def _presence_strip_html(chain: List[bool], targets: Dict[str, bool],
                         detailed: bool = False) -> str:
    flags = list(chain) + [bool(targets.get(t)) for t in ("GTV", "CTV", "PTV")]
    cells = []
    for (label, desc), present in zip(_PRESENCE, flags):
        cls = "on" if present else "off"
        mark = "✓" if present else "—"
        state = "present" if present else "absent"
        cells.append(
            f"<span class='pcell {cls}' title='{_esc(desc)}: {state}'>"
            f"<span class='pmark' aria-hidden='true'>{mark}</span>"
            f"<span class='plabel'>{_esc(label)}</span></span>"
        )
    extra = " detailed" if detailed else ""
    return f"<span class='presence{extra}'>{''.join(cells)}</span>"


def _verdict_pill(status: str) -> str:
    # Sober status tag (NHS/USWDS style): the verdict word carries the meaning, a small
    # square swatch carries the colour. Legible without colour (the word + a per-status
    # class + an aria-label), so it satisfies WCAG without a decorative glyph.
    color = VERDICT_COLORS.get(status, "#5b6775")
    return (f"<span class='pill pill-{_esc(status)}' data-verdict='{_esc(status)}' "
            f"style='--pill:{color}' aria-label=\"verdict: {_esc(status)}\">"
            f"{_esc(status)}</span>")


def _topbar_html(manifest: dict) -> str:
    items = [
        ("root", manifest.get("root", "")),
        ("files seen", manifest.get("n_files_seen", 0)),
        ("DICOM indexed", manifest.get("n_dicom_indexed", 0)),
        ("patients", manifest.get("n_patients", 0)),
        ("studies", manifest.get("n_studies", 0)),
        ("generated", manifest.get("generated_utc", "")),
    ]
    chips = "".join(
        f"<span class='manifest-item'><span class='mk'>{_esc(k)}</span>"
        f"<span class='mv'>{_esc(v)}</span></span>"
        for k, v in items
    )
    return f"""<header class="topbar">
  <div class="brand">
    <span class="title">DICOM<span class="thin">_discovery</span></span>
    <span class="subtitle">cohort quality-control report</span>
  </div>
  <div class="ruo" role="note" aria-label="usage restriction">{_esc(RUO_TEXT)}</div>
  <div class="manifest">{chips}</div>
</header>"""


def _kpi_html(kpis: dict) -> str:
    # A calm horizontal summary bar (one segmented strip), not a grid of cards. Each segment
    # is a count + label; the actionable verdicts are tinted, the rest stay neutral.
    # The cohort-size and verdict segments are clickable filter chips: clicking one narrows
    # both the RT table and the cohort map to the patients it counts ("patients" resets).
    v = kpis["verdicts"]
    pct = kpis["pct_complete"]
    pct_str = f"{pct}%" if pct is not None else "—"
    to_review = v["WARN"] + v["INCOMPLETE"]

    def chip(num, lab, status, cls="") -> str:
        pressed = "true" if status == "ALL" else "false"
        return (f"<button type='button' class='kpi kpi-btn' data-status='{status}' "
                f"aria-pressed='{pressed}'>"
                f"<span class='kpi-num'>{num}</span>"
                f"<span class='kpi-lab {cls}'>{_esc(lab)}</span></button>")

    def static(num, lab, cls="") -> str:
        return (f"<div class='kpi kpi-static'><span class='kpi-num'>{num}</span>"
                f"<span class='kpi-lab {cls}'>{_esc(lab)}</span></div>")

    cells = [
        chip(kpis["n_patients"], "patients", "ALL"),
        chip(v["OK"], "OK", "OK", "ok"),
        chip(v["WARN"], "WARN", "WARN", "warn"),
        chip(v["INCOMPLETE"], "INCOMPLETE", "INCOMPLETE", "incomplete"),
        chip(v["NO_RT"], "NO_RT", "NO_RT", "nort"),
        chip(to_review, "to review", "REVIEW", "act"),
        static(pct_str, "cohort complete"),
    ]
    return f'<section class="kpis">{"".join(cells)}</section>'


def _rt_table_html(rows: List[dict], findings: Dict[str, List[dict]]) -> str:
    body = []
    for i, r in enumerate(rows):
        pid = r["patient_id"]
        frag = "<span class='tag frag'>fragmented</span>" if r["fragmented"] else ""
        detail = findings.get(pid, [])
        has_detail = bool(detail)
        caret = "▸" if has_detail else ""
        # filter text: everything searchable, lowercased, in a data attribute.
        ftext = " ".join([pid, r["rt_status"], r["reason"], r["action"],
                          "fragmented" if r["fragmented"] else ""]).lower()
        body.append(
            f"<tr class='prow{' has-detail' if has_detail else ''}' "
            f"data-filter=\"{_esc(ftext)}\" data-row='{i}' "
            f"data-pid='{_esc(pid)}' data-status='{_esc(r['rt_status'])}' "
            f"data-nstudies='{r['n_studies']}' data-nrt='{r['n_rt_studies']}'>"
            f"<td class='c-caret'>{caret}</td>"
            f"<td class='c-pid mono'>{_esc(pid)}</td>"
            f"<td class='c-status'>{_verdict_pill(r['rt_status'])}{frag}</td>"
            f"<td class='c-presence'>{_presence_strip_html(r['chain'], r['targets'])}</td>"
            f"<td class='c-num mono'>{r['n_studies']}</td>"
            f"<td class='c-num mono'>{r['n_rt_studies']}</td>"
            f"<td class='c-reason'>{_esc(r['reason']) or '<span class=dim>—</span>'}</td>"
            f"<td class='c-action'>{_esc(r['action']) or '<span class=dim>—</span>'}</td>"
            "</tr>"
        )
        if has_detail:
            detail_html = []
            for s in detail:
                fitems = []
                for f in s["findings"]:
                    sev = f["severity"] or "INFO"
                    fitems.append(
                        f"<li><span class='badge sev-{_esc(sev.lower())}'>{_esc(sev)}</span>"
                        f"<span class='badge conf'>{_esc(f['confidence'] or '—')}</span>"
                        f"<span class='ftext'>{_esc(f['text'])}</span></li>"
                    )
                findings_block = (f"<ul class='findings'>{''.join(fitems)}</ul>"
                                  if fitems else "<p class='dim'>no findings — chain resolves cleanly</p>")
                detail_html.append(
                    f"<div class='study'><div class='study-head'>"
                    f"<span class='mono'>{_esc(s['study_date']) or 'undated'}</span>"
                    f"{_verdict_pill(s['rt_status'])}"
                    f"<span class='dim mono'>{s['n_roi']} ROI</span></div>"
                    f"{findings_block}</div>"
                )
            # Drill-down opens with a recap: the verdict, the full data-presence strip, and
            # the recommended action — then the per-study findings beneath it.
            recap = (
                "<div class='detail-summary'>"
                f"<div class='detail-meta'><b>Verdict</b>{_verdict_pill(r['rt_status'])}{frag}</div>"
                f"<div class='detail-meta'><b>Data presence</b>"
                f"{_presence_strip_html(r['chain'], r['targets'], detailed=True)}</div>"
                f"<div class='detail-meta'><b>Issue</b>"
                f"<span>{_esc(r['reason']) or '—'}</span></div>"
                f"<div class='detail-meta'><b>Recommended action</b>"
                f"<span>{_esc(r['action']) or '—'}</span></div>"
                "</div>"
            )
            body.append(
                f"<tr class='drow' data-detail-for='{i}' hidden><td colspan='8'>"
                f"<div class='detail'>{recap}<div class='detail-title'>"
                f"Per-study findings — {_esc(pid)}</div>{''.join(detail_html)}</div>"
                "</td></tr>"
            )
    rows_html = "".join(body) or (
        "<tr><td colspan='8' class='dim' style='text-align:center;padding:24px'>"
        "no patients</td></tr>")
    return f"""<div class="toolbar">
  <input type="search" id="rt-filter" class="filter" placeholder="Filter patients…"
         aria-label="filter RT table">
  <button type="button" class="btn" id="rt-export">Export CSV</button>
  <span class="hint">Click a row marked <span class="cue">▸</span> to open its findings.</span>
</div>
<table class="grid" id="rt-table">
  <thead><tr>
    <th class="c-caret" aria-hidden="true"></th>
    <th class="sortable" data-key="pid" data-type="text">Patient</th>
    <th class="sortable" data-key="status" data-type="text">Verdict</th>
    <th>Data presence</th>
    <th class="sortable num" data-key="nstudies" data-type="num">Studies</th>
    <th class="sortable num" data-key="nrt" data-type="num">RT studies</th>
    <th>Issue</th>
    <th>Action</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>"""


def _styles() -> str:
    # Design language: a sober clinical *document* — the look of hospital / clinical-trial
    # data software, not a dashboard. A quiet grey paper surface, one discreet slate-blue
    # accent used only for structure, and colour spent solely where it carries meaning
    # (the four verdicts, the five completeness states, present/absent in the data strip).
    # Plain bordered tables, NHS/USWDS-style status tags, no gradients/glow/animation.
    # The whole palette is governed from the :root variables below.
    return """
:root{
  /* Surfaces — a quiet, paper-like stack of greys. */
  --bg:#e9edf1; --surface:#ffffff; --surface-2:#f4f7f9; --surface-3:#eaeef2;
  --line:#dde3e9; --line-strong:#c6cfd8;
  /* Ink */
  --ink:#1d2733; --text:#2c3744; --muted:#5c6877; --dim:#8794a1;
  /* One discreet institutional accent (slate blue) — used for structure, never decoration. */
  --accent:#2a5d8f; --accent-soft:#eef3f8; --accent-line:#bdd0e1;
  /* Functional colours, spent only where they carry meaning. */
  --ok:#2e7d4f; --warn:#946000; --incomplete:#b42318; --nort:#5b6775; --absent:#b0584f;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --radius:6px; --radius-sm:4px;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:var(--bg);color:var(--text);font-family:var(--sans);
  font-size:13.5px;line-height:1.5;-webkit-font-smoothing:antialiased;
  font-feature-settings:"tnum" 1;
}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.dim{color:var(--dim)}

/* ---- topbar: a clinical document header ---- */
.topbar{
  position:sticky;top:0;z-index:30;
  display:flex;align-items:center;gap:18px;flex-wrap:wrap;
  padding:13px 30px;background:var(--surface);
  border-bottom:1px solid var(--line-strong);
}
.brand{display:flex;align-items:baseline;gap:11px}
.title{font-size:15px;font-weight:650;color:var(--ink);letter-spacing:0}
.title .thin{color:var(--muted);font-weight:400}
.subtitle{
  color:var(--muted);font-size:12px;
  border-left:1px solid var(--line-strong);padding-left:11px;align-self:center;
}
/* RUO: a plain bordered note — a regulatory line, not a badge. */
.ruo{
  margin-left:auto;font-size:11px;color:var(--muted);
  background:var(--surface-2);border:1px solid var(--line);
  padding:4px 11px;border-radius:var(--radius-sm);
}
.manifest{
  display:flex;flex-wrap:wrap;gap:6px 30px;width:100%;
  padding-top:11px;margin-top:3px;border-top:1px solid var(--line);
}
.manifest-item{display:flex;flex-direction:column;gap:1px;line-height:1.25}
.mk{font-size:10px;color:var(--dim);letter-spacing:.01em}
.mv{
  font-family:var(--mono);font-size:11.5px;color:var(--text);
  max-width:54ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}

/* ---- layout ---- */
main{max-width:1240px;margin:0 auto;padding:26px 30px 48px}

/* ---- KPI summary bar (segmented strip, not cards) ---- */
.kpis{
  display:flex;flex-wrap:wrap;align-items:stretch;
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  margin-bottom:26px;overflow:hidden;
}
.kpi{
  display:flex;flex-direction:column;gap:5px;padding:13px 22px;
  border-right:1px solid var(--line);min-width:118px;
}
.kpi:last-child{border-right:none;margin-left:auto;text-align:right;align-items:flex-end}
.kpi-num{font-size:21px;font-weight:600;color:var(--ink);letter-spacing:-.01em;line-height:1}
.kpi-lab{font-size:11.5px;color:var(--muted)}
.kpi-lab.ok{color:var(--ok)}
.kpi-lab.warn{color:var(--warn)}
.kpi-lab.incomplete{color:var(--incomplete)}
.kpi-lab.nort{color:var(--nort)}
.kpi-lab.act{color:var(--warn);font-weight:600}
/* Clickable filter chips: the count segments narrow the table + map by status. */
.kpi-btn{
  appearance:none;border:0;border-right:1px solid var(--line);
  font:inherit;text-align:left;color:inherit;background:var(--surface);
  cursor:pointer;transition:background .12s,box-shadow .12s;
}
.kpi-btn:hover{background:var(--surface-2)}
.kpi-btn:focus-visible{outline:none;box-shadow:inset 0 0 0 2px var(--accent-soft)}
.kpi-btn[aria-pressed="true"]{
  background:var(--accent-soft);box-shadow:inset 0 -2px 0 var(--accent);
}
.kpi-static{margin-left:auto;text-align:right;align-items:flex-end}

/* ---- tabs ---- */
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--line-strong);margin-bottom:22px}
.tab{
  appearance:none;background:none;border:none;font:inherit;color:var(--muted);
  font-size:13px;font-weight:550;padding:9px 18px;cursor:pointer;
  border-bottom:2px solid transparent;margin-bottom:-1px;
}
.tab:hover{color:var(--text)}
.tab[aria-selected="true"]{color:var(--accent);border-bottom-color:var(--accent)}
.tab .count{font-family:var(--mono);font-size:11px;color:var(--dim);margin-left:7px}
.panel[hidden]{display:none}

/* ---- toolbar ---- */
.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.filter{
  background:var(--surface);border:1px solid var(--line-strong);color:var(--text);
  font:inherit;font-size:13px;padding:7px 12px;border-radius:var(--radius-sm);min-width:240px;
}
.filter::placeholder{color:var(--dim)}
.filter:focus-visible{outline:none;border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
.btn{
  background:var(--surface);border:1px solid var(--line-strong);color:var(--text);
  font:inherit;font-size:13px;font-weight:550;padding:7px 14px;border-radius:var(--radius-sm);cursor:pointer;
}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:focus-visible{outline:none;box-shadow:0 0 0 2px var(--accent-soft)}
.hint{color:var(--dim);font-size:12px;margin-left:auto}
.cue{color:var(--accent)}

/* ---- table ---- */
.grid{
  width:100%;border-collapse:collapse;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;
}
.grid thead th{
  background:var(--surface-2);text-align:left;
  font-family:var(--sans);font-size:11.5px;font-weight:600;color:var(--muted);
  letter-spacing:0;padding:11px 14px;border-bottom:1px solid var(--line-strong);white-space:nowrap;
}
.grid th.num,.grid td.c-num{text-align:right}
.grid th.sortable{cursor:pointer;user-select:none}
.grid th.sortable:hover{color:var(--text)}
.grid th.sort-asc::after{content:" \2191";color:var(--accent)}
.grid th.sort-desc::after{content:" \2193";color:var(--accent)}
.grid td{padding:10px 14px;border-bottom:1px solid var(--line);vertical-align:middle}
.grid tbody tr:last-child td{border-bottom:none}
.grid tbody tr:hover{background:var(--surface-2)}
.prow.has-detail{cursor:pointer}
.prow.open{background:var(--accent-soft)}
.prow.open:hover{background:var(--accent-soft)}
.c-caret{width:22px;color:var(--accent);text-align:center;font-size:11px}
.c-pid{font-family:var(--mono);font-weight:600;color:var(--ink)}
.c-reason{color:var(--muted);font-size:12.5px;max-width:30ch}
.c-action{color:var(--text);font-size:12.5px;max-width:28ch}

/* ---- verdict tag (sober, NHS/USWDS style) ---- */
.pill{
  display:inline-flex;align-items:center;gap:6px;
  font-size:11.5px;font-weight:600;letter-spacing:.01em;
  color:color-mix(in srgb,var(--pill) 78%,#101820);
  background:color-mix(in srgb,var(--pill) 9%,#fff);
  border:1px solid color-mix(in srgb,var(--pill) 26%,transparent);
  padding:2px 9px;border-radius:var(--radius-sm);white-space:nowrap;
}
.pill::before{content:"";width:7px;height:7px;border-radius:2px;background:var(--pill);flex:none}
.tag.frag{
  margin-left:8px;font-size:10.5px;color:var(--warn);
  border:1px solid color-mix(in srgb,var(--warn) 38%,transparent);
  padding:1px 6px;border-radius:var(--radius-sm);
}

/* ---- data-presence strip (CT STR PLAN DOSE / GTV CTV PTV) ---- */
.presence{display:inline-flex;gap:3px;flex-wrap:nowrap}
.pcell{
  display:inline-flex;flex-direction:column;align-items:center;justify-content:center;
  min-width:38px;padding:3px 4px;border-radius:var(--radius-sm);line-height:1.15;
  border:1px solid var(--line-strong);background:var(--surface-2);
}
.pcell .pmark{font-size:11px;font-weight:600;color:var(--muted)}
.pcell .plabel{font-size:8.5px;letter-spacing:.02em;color:var(--muted)}
.pcell.on{background:#f0f6f1;border-color:#cce0d2}
.pcell.on .pmark{color:var(--ok)}
.pcell.on .plabel{color:#43684f}
.pcell.off{background:var(--surface);border:1px dashed var(--line-strong)}
.pcell.off .pmark{color:var(--absent)}
.pcell.off .plabel{color:var(--dim)}
/* The fifth cell (GTV) starts the target group — a hair more separation. */
.pcell:nth-child(5){margin-left:8px}
.presence.detailed{gap:5px;flex-wrap:wrap}
.presence.detailed .pcell{min-width:50px;padding:7px 8px}
.presence.detailed .pcell .pmark{font-size:13px}
.presence.detailed .pcell .plabel{font-size:10px}

/* ---- drill-down detail ---- */
.detail{
  background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius);
  margin:2px 0 8px;padding:16px 18px;
}
.detail-summary{
  display:flex;flex-wrap:wrap;align-items:flex-start;gap:16px 30px;
  padding-bottom:14px;margin-bottom:14px;border-bottom:1px solid var(--line);
}
.detail-meta{display:flex;flex-direction:column;gap:6px;font-size:12.5px;max-width:34ch}
.detail-meta>b{color:var(--muted);font-weight:600;font-size:10.5px;letter-spacing:.03em;text-transform:uppercase}
.detail-title{font-size:11px;font-weight:650;color:var(--dim);letter-spacing:.04em;
  text-transform:uppercase;margin-bottom:12px}
.study{padding:11px 0;border-top:1px solid var(--line)}
.study:first-of-type{border-top:none;padding-top:2px}
.study-head{display:flex;align-items:center;gap:10px;margin-bottom:8px;font-size:12.5px}
.findings{list-style:none;display:flex;flex-direction:column;gap:7px}
.findings li{display:flex;align-items:baseline;gap:8px;font-size:12.5px}
.badge{font-size:10px;font-weight:600;padding:1px 7px;border-radius:var(--radius-sm);flex:none}
.sev-error{color:var(--incomplete);background:color-mix(in srgb,var(--incomplete) 9%,#fff);
  border:1px solid color-mix(in srgb,var(--incomplete) 26%,transparent)}
.sev-warning{color:var(--warn);background:color-mix(in srgb,var(--warn) 10%,#fff);
  border:1px solid color-mix(in srgb,var(--warn) 28%,transparent)}
.sev-info{color:var(--accent);background:var(--accent-soft);
  border:1px solid color-mix(in srgb,var(--accent) 24%,transparent)}
.badge.conf{color:var(--dim);border:1px solid var(--line-strong)}
.ftext{color:var(--text)}

/* ---- completeness ---- */
.c-bar{display:flex;align-items:center;gap:10px;min-width:170px}
.bar{flex:1;height:6px;background:var(--surface-3);border-radius:999px;overflow:hidden}
.bar-fill{display:block;height:100%;background:var(--ok)}
.pct{font-family:var(--mono);font-size:11.5px;color:var(--muted);min-width:42px;text-align:right}
.legend{display:flex;flex-wrap:wrap;gap:7px 18px;margin:10px 0 14px;font-size:12px}
.leg{display:inline-flex;align-items:center;gap:6px}
.sw{width:12px;height:12px;border-radius:2px;border:1px solid var(--line-strong);flex:none}
.leg b{font-weight:600;font-size:11.5px}

/* ---- section heading / plot / note ---- */
.section-title{font-size:13.5px;font-weight:650;color:var(--ink);margin:24px 0 12px}
.maphint{margin:0 0 12px;color:var(--muted);font-size:12.5px}
.plot{
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:10px;margin-bottom:18px;overflow:hidden;
}
.note{
  color:var(--muted);padding:16px 18px;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--radius);font-size:13px;
}

/* ---- footer ---- */
footer{
  color:var(--dim);font-size:11.5px;text-align:center;
  padding:26px 20px 40px;border-top:1px solid var(--line);margin-top:26px;line-height:1.7;
}
footer b{color:var(--muted)}

/* ---- reduced motion ---- */
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}

/* ---- responsive ---- */
@media (max-width:760px){
  main{padding:16px}
  .kpi{min-width:50%;border-right:none}
  .kpi:last-child{margin-left:0;text-align:left;align-items:flex-start}
  .hint{display:none}
  .mv{max-width:24ch}
}
"""


def _script() -> str:
    # Vanilla JS: tabs, sort, filter, row drill-down toggle, CSV export. No framework.
    return r"""
(function(){
  "use strict";
  // ---- tabs ----
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.tab'));
  function selectTab(id){
    tabs.forEach(function(t){
      var on = t.getAttribute('data-tab') === id;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll('.panel').forEach(function(p){
      p.hidden = p.getAttribute('data-panel') !== id;
    });
    // Plotly figures laid out while hidden render at width 0 — resize on reveal.
    if(window.Plotly){
      document.querySelectorAll('.panel:not([hidden]) .plotly-graph-div').forEach(function(g){
        try{ Plotly.Plots.resize(g); }catch(e){}
      });
    }
  }
  tabs.forEach(function(t){
    t.addEventListener('click', function(){ selectTab(t.getAttribute('data-tab')); });
  });
  // ---- RT table filtering: one state shared by the text search and the KPI chips ----
  var rtState = {status: 'ALL', q: ''};
  function matchStatus(st){
    if(rtState.status === 'ALL') return true;
    if(rtState.status === 'REVIEW') return st === 'WARN' || st === 'INCOMPLETE';
    return st === rtState.status;
  }
  function applyRtFilter(){
    var table = document.getElementById('rt-table');
    if(!table) return;
    table.querySelectorAll('tbody tr.prow').forEach(function(tr){
      var st = tr.getAttribute('data-status') || '';
      var hay = tr.getAttribute('data-filter') || '';
      var show = matchStatus(st) && hay.indexOf(rtState.q) !== -1;
      tr.hidden = !show;
      var idx = tr.getAttribute('data-row');
      if(idx !== null){
        var d = table.querySelector("tr[data-detail-for='" + idx + "']");
        if(d && !show){ d.hidden = true; tr.classList.remove('open'); }
      }
    });
  }
  var rtFilterInput = document.getElementById('rt-filter');
  if(rtFilterInput){
    rtFilterInput.addEventListener('input', function(){
      rtState.q = rtFilterInput.value.trim().toLowerCase();
      applyRtFilter();
    });
  }
  // ---- drill-down ----
  var rtTable = document.getElementById('rt-table');
  if(rtTable){
    rtTable.querySelectorAll('tbody tr.prow.has-detail').forEach(function(tr){
      tr.addEventListener('click', function(){
        var idx = tr.getAttribute('data-row');
        var d = rtTable.querySelector("tr[data-detail-for='" + idx + "']");
        if(!d) return;
        d.hidden = !d.hidden;
        tr.classList.toggle('open', !d.hidden);
        tr.querySelector('.c-caret').textContent = d.hidden ? '▸' : '▾';
      });
    });
  }
  // ---- sorting ----
  function cellVal(tr, key, type){
    var map = {pid:'pid', status:'status', nstudies:'nstudies', nrt:'nrt',
               patient:'patient', pct:'pct', missing:'missing'};
    var attr = map[key];
    if(attr){ var v = tr.getAttribute('data-' + attr); if(v !== null) return v; }
    return '';
  }
  function bindSort(tableId){
    var table = document.getElementById(tableId);
    if(!table) return;
    var ths = table.querySelectorAll('th.sortable');
    ths.forEach(function(th, ci){
      th.addEventListener('click', function(){
        var key = th.getAttribute('data-key');
        var type = th.getAttribute('data-type');
        var asc = !th.classList.contains('sort-asc');
        ths.forEach(function(o){ o.classList.remove('sort-asc','sort-desc'); });
        th.classList.add(asc ? 'sort-asc' : 'sort-desc');
        var tbody = table.querySelector('tbody');
        // gather primary rows (+ their detail rows) so detail follows its parent
        var groups = [];
        Array.prototype.slice.call(tbody.children).forEach(function(tr){
          if(tr.classList.contains('drow')) return;
          var detail = null;
          var idx = tr.getAttribute('data-row');
          if(idx !== null) detail = tbody.querySelector("tr[data-detail-for='" + idx + "']");
          groups.push({row:tr, detail:detail});
        });
        var numeric = (type === 'num' || type === 'num-cell');
        groups.sort(function(a,b){
          var va = numericOrText(a.row, key, ci, table, numeric);
          var vb = numericOrText(b.row, key, ci, table, numeric);
          if(va < vb) return asc ? -1 : 1;
          if(va > vb) return asc ? 1 : -1;
          return 0;
        });
        groups.forEach(function(g){ tbody.appendChild(g.row); if(g.detail) tbody.appendChild(g.detail); });
      });
    });
  }
  function numericOrText(tr, key, colIndex, table, numeric){
    var v = cellVal(tr, key);
    if(v === '' ){
      // fall back to cell text for completeness columns (expected/present/missing)
      var cell = tr.children[colIndex];
      v = cell ? cell.textContent.replace('%','').trim() : '';
    }
    if(numeric){ var n = parseFloat(v); return isNaN(n) ? -Infinity : n; }
    return String(v).toLowerCase();
  }
  bindSort('rt-table');
  // ---- CSV export ----
  function exportCsv(tableId, filename){
    var table = document.getElementById(tableId);
    if(!table) return;
    var rows = [];
    var headers = [];
    table.querySelectorAll('thead th').forEach(function(th){
      var t = th.textContent.trim(); if(t) headers.push(t);
    });
    rows.push(headers);
    table.querySelectorAll('tbody tr').forEach(function(tr){
      if(tr.classList.contains('drow')) return;
      if(tr.hidden) return;
      var cells = [];
      Array.prototype.slice.call(tr.children).forEach(function(td){
        if(td.classList.contains('c-caret')) return;
        cells.push(td.textContent.replace(/\s+/g,' ').trim());
      });
      if(cells.join('').length) rows.push(cells);
    });
    var csv = rows.map(function(r){
      return r.map(function(c){ return '"' + String(c).replace(/"/g,'""') + '"'; }).join(',');
    }).join('\n');
    var blob = new Blob([csv], {type:'text/csv;charset=utf-8;'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = filename;
    a.rel = 'noopener';
    document.body.appendChild(a); a.click();
    // Defer cleanup: revoking the object URL (or removing the anchor) synchronously right
    // after click can abort the download before it starts in some browsers (notably Firefox).
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
  }
  var rtExp = document.getElementById('rt-export');
  if(rtExp) rtExp.addEventListener('click', function(){ exportCsv('rt-table','rt_integrity.csv'); });

  // ---- cohort map: narrow the plotted points to a set of patients ----
  var MAP_ORIG = null, MAP_ALLPIDS = null;
  function mapDiv(){ return document.querySelector('.plotly-graph-div'); }
  function ensureMapCapture(gd){
    if(MAP_ORIG) return;
    MAP_ORIG = (gd.data || []).map(function(t){
      return {x:(t.x||[]).slice(), y:(t.y||[]).slice(),
              customdata:(t.customdata ? t.customdata.slice() : null)};
    });
    var cats = gd.layout && gd.layout.yaxis && gd.layout.yaxis.categoryarray;
    if(!cats){
      var seen = {}; cats = [];
      MAP_ORIG.forEach(function(o){ o.y.forEach(function(p){ p=String(p);
        if(!seen[p]){ seen[p]=1; cats.push(p); } }); });
    }
    MAP_ALLPIDS = cats.map(String);
  }
  function filterMap(sel){ // sel: a Set of patient ids, or null for "all"
    var gd = mapDiv();
    if(!gd || !window.Plotly || !gd.data) return;
    ensureMapCapture(gd);
    var xs=[], ys=[], cds=[], idxs=[];
    MAP_ORIG.forEach(function(o, ti){
      idxs.push(ti);
      if(!sel){ xs.push(o.x); ys.push(o.y); cds.push(o.customdata); return; }
      var nx=[], ny=[], nc=o.customdata ? [] : null;
      for(var i=0;i<o.y.length;i++){
        if(sel.has(String(o.y[i]))){
          nx.push(o.x[i]); ny.push(o.y[i]); if(nc) nc.push(o.customdata[i]);
        }
      }
      xs.push(nx); ys.push(ny); cds.push(nc);
    });
    Plotly.restyle(gd, {x:xs, y:ys, customdata:cds}, idxs);
    var cats = sel ? MAP_ALLPIDS.filter(function(p){ return sel.has(p); }) : MAP_ALLPIDS;
    Plotly.relayout(gd, {'yaxis.categoryarray':cats, 'yaxis.type':'category',
                         'yaxis.autorange':'reversed'});
  }
  // ---- KPI filter chips: drive the table + map together ----
  function selectedPids(status){
    if(status === 'ALL') return null;
    var set = new Set();
    document.querySelectorAll('#rt-table tbody tr.prow').forEach(function(tr){
      var st = tr.getAttribute('data-status') || '';
      var ok = (status === 'REVIEW') ? (st==='WARN' || st==='INCOMPLETE') : (st===status);
      if(ok) set.add(String(tr.getAttribute('data-pid')));
    });
    return set;
  }
  var kpiBtns = Array.prototype.slice.call(document.querySelectorAll('.kpi-btn'));
  kpiBtns.forEach(function(b){
    b.addEventListener('click', function(){
      var status = b.getAttribute('data-status');
      kpiBtns.forEach(function(o){ o.setAttribute('aria-pressed', o===b ? 'true' : 'false'); });
      rtState.status = status;
      applyRtFilter();
      filterMap(selectedPids(status));
    });
  });
})();
"""


def render_cohort_report(rt_study_df: pd.DataFrame,
                         rollup_df: pd.DataFrame,
                         comp_state: pd.DataFrame,
                         comp_hover: pd.DataFrame,
                         comp_long: pd.DataFrame,
                         manifest: dict,
                         protocol: Protocol,
                         out_path: str,
                         table: Optional[pd.DataFrame] = None) -> str:
    """Assemble and write the unified self-contained cohort report; return the path.

    ``table`` is the canonical index table; when given it drives the interactive cohort
    timeline map (legended + source-traceable on hover). All interactivity is inline
    vanilla JS and Plotly is embedded — the file opens by double-click on an air-gapped
    network with no external fetches.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    kpis = build_kpis(rollup_df, comp_long)
    # Present the registry as a work queue: WARN/INCOMPLETE first (counts are order-free).
    rt_rows = rollup_rows(order_for_review(rollup_df))
    findings = study_findings(rt_study_df)

    n_rt = len(rt_rows)
    # The cohort timeline map is the single Plotly figure, so it always carries the bundle.
    cohort_map = _timeline_map_html(table, embed_js=True)
    title = f"DICOM_discovery cohort report — {protocol.name}"

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_styles()}</style>
</head><body>
{_topbar_html(manifest)}
<main>
  {_kpi_html(kpis)}
  <div class="tabs" role="tablist" aria-label="report sections">
    <button class="tab" role="tab" data-tab="rt" aria-selected="true">
      RT integrity<span class="count">{n_rt}</span></button>
    <button class="tab" role="tab" data-tab="map" aria-selected="false">
      Cohort map</button>
  </div>

  <section class="panel" role="tabpanel" data-panel="rt">
    {_rt_table_html(rt_rows, findings)}
  </section>

  <section class="panel" role="tabpanel" data-panel="map" hidden>
    <p class="hint maphint">One point per patient · study date · modality — coloured by modality
      (legend above). Hover a point for its date, series/study counts and its DICOM
      <b>source path(s)</b>.</p>
    <div class="plot">{cohort_map}</div>
  </section>
</main>
<footer>
  <b>{_esc(RUO_TEXT)}</b><br>
  Header-only cohort QC over synthetic or de-identified DICOM. Not a clinical safety check.
  Generated by DICOM_discovery.
</footer>
<script>{_script()}</script>
</body></html>"""

    out.write_text(page, encoding="utf-8")
    LOG.info("Cohort report -> %s (%d patients)", out, kpis["n_patients"])
    return str(out)

