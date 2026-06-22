# -*- coding: utf-8 -*-
"""
reports.py — Génère un rapport HTML autonome « type software » (dashboard) à partir
de la carte cohorte existante + du tableau d'intégrité RT.

Principe : on NE MODIFIE PAS les informations disponibles. Tout le contenu de la carte
cohorte (timeline Plotly, résumés RT / clinique / DICOM par timepoint) est conservé tel
quel dans un onglet « Carte cohorte ». On ajoute uniquement l'habillage produit :
barre d'en-tête, cartes KPI, navigation par onglets, et un tableau d'intégrité RT
interactif (tri, recherche, filtre par sévérité, export CSV, impression).

Sortie : `file_discovery_report.html` (un seul fichier, hors-ligne).
"""
from __future__ import annotations

import html
import re
from collections import Counter
from pathlib import Path
from typing import List, Optional

import pandas as pd

DESIGN_CSS = """
:root{--ink:#1e293b;--muted:#64748b;--line:#e2e8f0;--accent:#2563eb;--ok:#16a34a;--warn:#d97706;--err:#dc2626;}
*{box-sizing:border-box}
body{margin:0;font-family:system-ui,'Segoe UI',Roboto,Arial,sans-serif;color:var(--ink);background:#f1f5f9}
.topbar{background:linear-gradient(90deg,#0f172a,#1e3a8a);color:#fff;padding:14px 22px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.topbar .logo{font-size:19px;font-weight:700;letter-spacing:.3px}
.topbar .meta{font-size:12px;color:#cbd5e1;line-height:1.5}
.topbar .spacer{flex:1}
.topbar .tool{background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:7px 12px;border-radius:8px;cursor:pointer;font-size:13px}
.topbar .tool:hover{background:#334155}
.kpis{display:flex;gap:14px;padding:18px 22px 6px;flex-wrap:wrap}
.kpi{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 18px;min-width:140px;box-shadow:0 1px 2px rgba(0,0,0,.04);cursor:pointer;transition:.15s}
.kpi:hover{box-shadow:0 6px 18px rgba(0,0,0,.09);transform:translateY(-1px)}
.kpi .v{font-size:27px;font-weight:700;line-height:1.1}
.kpi .l{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:4px}
.kpi.ok .v{color:var(--ok)}.kpi.warn .v{color:var(--warn)}.kpi.err .v{color:var(--err)}.kpi.acc .v{color:var(--accent)}
.tabs{display:flex;gap:2px;padding:6px 22px 0;border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:5}
.tabbtn{padding:12px 18px;border:none;background:none;font-size:14px;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent}
.tabbtn.active{color:var(--accent);border-bottom-color:var(--accent);font-weight:600}
.tabpane{display:none;padding:22px}.tabpane.active{display:block}
.panel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:0 0 18px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.toolbar input,.toolbar select{padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px}
.toolbar .grow{flex:1}
.btn{padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:#fff;cursor:pointer;font-size:13px}
.btn:hover{background:#f8fafc}
.tablewrap{max-height:72vh;overflow:auto;border:1px solid var(--line);border-radius:10px}
table.dt{border-collapse:separate;border-spacing:0;width:100%;font-size:13px}
table.dt th,table.dt td{border-bottom:1px solid var(--line);padding:7px 10px;text-align:left;white-space:nowrap}
table.dt th{cursor:pointer;user-select:none;background:#f8fafc;position:sticky;top:0;z-index:1}
table.dt th:hover{background:#eef2f7}
table.dt tbody tr:hover{background:#f8fbff}
.chip{padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;color:#fff;display:inline-block}
.chip.OK{background:var(--ok)}.chip.WARN{background:var(--warn)}.chip.INCOMPLET{background:var(--err)}
.yes{color:var(--ok);font-weight:700}.no{color:var(--err);font-weight:700}
.mut{color:var(--muted);font-size:11px}
.summary-table{border-collapse:collapse}.summary-table td,.summary-table th{border:1px solid var(--line);padding:4px 8px;font-size:12px}
h1{font-size:18px;margin:.2em 0}h2{font-size:15px;margin:.2em 0 .7em}h3{font-size:13px;margin:.6em 0 .3em}
.subtitle{color:var(--muted);font-size:12px;margin-bottom:10px}
@media print{.topbar,.tabs,.toolbar,.kpis{-webkit-print-color-adjust:exact}.tabpane{display:block!important}}
"""

DASHBOARD_JS = """
function showTab(id,btn){
  document.querySelectorAll('.tabpane').forEach(function(p){p.classList.remove('active');});
  document.querySelectorAll('.tabbtn').forEach(function(b){b.classList.remove('active');});
  var pane=document.getElementById(id); if(pane)pane.classList.add('active');
  if(btn)btn.classList.add('active');
  if(id==='tab-cohort'){setTimeout(function(){
    document.querySelectorAll('#tab-cohort .plotly-graph-div').forEach(function(g){if(window.Plotly)Plotly.Plots.resize(g);});
  },80);}
}
function filterRT(){
  var q=(document.getElementById('rt-search').value||'').toLowerCase();
  var s=document.getElementById('rt-sev').value;
  var nv=0;
  document.querySelectorAll('#rt-table tbody tr').forEach(function(tr){
    var okS=(s==='all'||tr.getAttribute('data-status')===s);
    var okT=(q===''||tr.textContent.toLowerCase().indexOf(q)>=0);
    var show=okS&&okT; tr.style.display=show?'':'none'; if(show)nv++;
  });
  var c=document.getElementById('rt-count'); if(c)c.textContent=nv+' patient(s)';
}
function setSev(s){var b=document.getElementById('btn-rt');showTab('tab-rt',b);document.getElementById('rt-sev').value=s;filterRT();}
function sortRT(th){
  var idx=[].indexOf.call(th.parentNode.children,th);
  var tb=document.querySelector('#rt-table tbody');
  var rows=[].slice.call(tb.querySelectorAll('tr'));
  var num=th.getAttribute('data-num')==='1';
  var asc=th.getAttribute('data-dir')!=='asc'; th.setAttribute('data-dir',asc?'asc':'desc');
  rows.sort(function(a,b){
    var x=a.children[idx].getAttribute('data-v'); if(x===null)x=a.children[idx].textContent;
    var y=b.children[idx].getAttribute('data-v'); if(y===null)y=b.children[idx].textContent;
    if(num){return ((parseFloat(x)||0)-(parseFloat(y)||0))*(asc?1:-1);}
    return x.localeCompare(y)*(asc?1:-1);
  });
  rows.forEach(function(r){tb.appendChild(r);});
}
function exportRT(){
  var trs=[].slice.call(document.querySelectorAll('#rt-table tr')).filter(function(tr){
    return tr.parentNode.tagName==='THEAD'||tr.style.display!=='none';});
  var csv=trs.map(function(tr){return [].slice.call(tr.children).map(function(td){
    var t=td.getAttribute('data-v'); if(t===null)t=td.textContent;
    return '"'+(''+t).replace(/"/g,'""')+'"';}).join(',');}).join('\\n');
  var a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(csv);
  a.download='rt_integrity_filtered.csv';a.click();
}
window.addEventListener('load',function(){filterRT();});
"""


def _extract_cohort_body(cohort_html: str) -> str:
    """Récupère le contenu <body> de la carte cohorte, sans le titre h1 ni le panneau RT
    statique (remplacé par le tableau interactif). Le reste est conservé à l'identique."""
    m = re.search(r"<body[^>]*>(.*)</body>", cohort_html, re.S)
    body = m.group(1) if m else cohort_html
    body = re.sub(r"<h1>.*?</h1>", "", body, count=1, flags=re.S)
    # retirer le panneau d'intégrité RT statique (table-only) s'il a été injecté
    body = re.sub(r"<div class='panel'><h2>Intégrité de la chaîne RT</h2>.*?</table></div>",
                  "", body, flags=re.S)
    return body


def _chk(b) -> str:
    return "<span class='yes'>✓</span>" if bool(b) else "<span class='no'>✗</span>"


def _rt_table_html(rt: pd.DataFrame) -> str:
    cols = [
        ("patient_id", "Patient", 0), ("rt_status", "Statut", 0),
        ("has_CT", "CT", 0), ("has_RTSTRUCT", "RS", 0), ("has_RTDOSE", "RD", 0), ("has_RTPLAN", "RP", 0),
        ("n_roi", "ROI", 1), ("roi_GTV", "GTV", 0), ("roi_CTV", "CTV", 0), ("roi_PTV", "PTV", 0),
        ("frame_consistent", "FoR cohér.", 0), ("for_ct_match", "FoR↔CT", 0),
        ("plan_links_struct", "plan→str", 0), ("dose_links_plan", "dose→plan", 0),
        ("n_fractions", "Fx", 1), ("warnings", "Alertes", 0),
    ]
    head = "".join(f"<th data-num='{n}' onclick='sortRT(this)'>{html.escape(lbl)} ⇅</th>" for _, lbl, n in cols)
    bool_cols = {"has_CT", "has_RTSTRUCT", "has_RTDOSE", "has_RTPLAN",
                 "roi_GTV", "roi_CTV", "roi_PTV", "frame_consistent",
                 "for_ct_match", "plan_links_struct", "dose_links_plan"}
    rows = []
    for _, r in rt.iterrows():
        tds = []
        for key, _lbl, _n in cols:
            v = r.get(key)
            if key == "rt_status":
                tds.append(f"<td data-v='{html.escape(str(v))}'><span class='chip {html.escape(str(v))}'>{html.escape(str(v))}</span></td>")
            elif key in bool_cols:
                tds.append(f"<td data-v='{1 if bool(v) else 0}'>{_chk(v)}</td>")
            elif key == "n_fractions":
                txt = "" if pd.isna(v) else str(int(v))
                tds.append(f"<td data-v='{txt or 0}'>{txt}</td>")
            elif key == "n_roi":
                tds.append(f"<td data-v='{int(v)}'>{int(v)}</td>")
            elif key == "warnings":
                txt = "" if pd.isna(v) else str(v)
                tds.append(f"<td class='mut'>{html.escape(txt)}</td>")
            else:
                tds.append(f"<td>{html.escape(str(v))}</td>")
        rows.append(f"<tr data-status='{html.escape(str(r.get('rt_status','')))}'>" + "".join(tds) + "</tr>")
    return (
        "<div class='toolbar'>"
        "<input id='rt-search' class='grow' placeholder='🔎 Rechercher (patient, alerte…)' oninput='filterRT()'>"
        "<select id='rt-sev' onchange='filterRT()'>"
        "<option value='all'>Tous les statuts</option>"
        "<option value='OK'>OK</option><option value='WARN'>WARN</option><option value='INCOMPLET'>INCOMPLET</option>"
        "</select>"
        "<span id='rt-count' class='mut'></span>"
        "<button class='btn' onclick='exportRT()'>⤓ Export CSV</button>"
        "<button class='btn' onclick='window.print()'>⎙ Imprimer</button>"
        "</div>"
        "<div class='tablewrap'><table class='dt' id='rt-table'><thead><tr>"
        + head + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _kpis_and_overview(rt: Optional[pd.DataFrame]):
    if rt is None or rt.empty:
        kpis = "<div class='kpi acc'><div class='v'>—</div><div class='l'>Intégrité RT non calculée</div></div>"
        overview = "<div class='panel'><h2>Synthèse</h2><p>Lancer avec <code>--rt-check</code> pour l'analyse d'intégrité RT.</p></div>"
        return kpis, overview
    n = len(rt)
    complete = int((rt["has_CT"] & rt["has_RTSTRUCT"] & rt["has_RTDOSE"] & rt["has_RTPLAN"]).sum())
    n_ok = int((rt["rt_status"] == "OK").sum())
    n_warn = int((rt["rt_status"] == "WARN").sum())
    n_inc = int((rt["rt_status"] == "INCOMPLET").sum())
    pct = lambda x: f"{100*x/n:.0f}%" if n else "—"
    kpis = (
        f"<div class='kpi acc' onclick=\"setSev('all')\"><div class='v'>{n}</div><div class='l'>Patients</div></div>"
        f"<div class='kpi acc' onclick=\"setSev('all')\"><div class='v'>{pct(complete)}</div><div class='l'>Chaîne RT complète</div></div>"
        f"<div class='kpi ok' onclick=\"setSev('OK')\"><div class='v'>{n_ok}</div><div class='l'>OK ({pct(n_ok)})</div></div>"
        f"<div class='kpi warn' onclick=\"setSev('WARN')\"><div class='v'>{n_warn}</div><div class='l'>Warning ({pct(n_warn)})</div></div>"
        f"<div class='kpi err' onclick=\"setSev('INCOMPLET')\"><div class='v'>{n_inc}</div><div class='l'>Incomplet ({pct(n_inc)})</div></div>"
    )
    # top alertes
    c = Counter()
    for w in rt["warnings"].dropna():
        for part in str(w).split(" ; "):
            key = part.split(" (")[0].split(":")[0].strip()
            if key:
                c[key] += 1
    alert_rows = "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k, v in c.most_common())
    pres = "".join(
        f"<tr><td>{lbl}</td><td>{int(rt[col].sum())}/{n}</td></tr>"
        for col, lbl in [("has_CT", "CT"), ("has_RTSTRUCT", "RTSTRUCT"), ("has_RTDOSE", "RTDOSE"),
                         ("has_RTPLAN", "RTPLAN"), ("roi_GTV", "GTV"), ("roi_CTV", "CTV"), ("roi_PTV", "PTV")]
    )
    overview = (
        "<div class='panel'><h2>Synthèse — intégrité de la chaîne RT</h2>"
        "<p class='subtitle'>Cliquez une carte KPI pour filtrer le tableau d'intégrité RT.</p>"
        "<div style='display:flex;gap:24px;flex-wrap:wrap'>"
        "<div><h3>Présence des objets / cibles</h3><table class='summary-table compact'>"
        "<tr><th>Élément</th><th>N</th></tr>" + pres + "</table></div>"
        "<div><h3>Alertes les plus fréquentes</h3><table class='summary-table compact'>"
        "<tr><th>Type</th><th>N</th></tr>" + (alert_rows or "<tr><td>aucune</td><td>0</td></tr>") + "</table></div>"
        "</div></div>"
    )
    return kpis, overview


def build_report(cohort_html_path, rt_df: Optional[pd.DataFrame], out_path,
                 title: str = "DICOM Discovery & RT QC", meta_lines: Optional[List[str]] = None) -> Path:
    """Assemble le dashboard HTML autonome."""
    cohort_html_path = Path(cohort_html_path)
    page = cohort_html_path.read_text(encoding="utf-8")
    orig_css = ""
    m = re.search(r"<style>(.*?)</style>", page, re.S)
    if m:
        orig_css = m.group(1)
    cohort_body = _extract_cohort_body(page)
    kpis, overview = _kpis_and_overview(rt_df)
    rt_tab = _rt_table_html(rt_df) if (rt_df is not None and not rt_df.empty) else \
        "<div class='panel'><p>Intégrité RT non disponible (relancer avec <code>--rt-check</code>).</p></div>"
    meta = "<br>".join(html.escape(x) for x in (meta_lines or []))

    page_out = f"""<!DOCTYPE html>
<html lang='fr'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{html.escape(title)}</title>
<style>{DESIGN_CSS}</style>
<style>{orig_css}</style>
</head><body>
<header class='topbar'>
  <div class='logo'>◰ EpiBrainRad · {html.escape(title)}</div>
  <div class='meta'>{meta}</div>
  <div class='spacer'></div>
  <button class='tool' onclick="window.print()">⎙ Imprimer / PDF</button>
</header>
<section class='kpis'>{kpis}</section>
<nav class='tabs'>
  <button id='btn-ov'     class='tabbtn active' onclick="showTab('tab-overview',this)">Vue d'ensemble</button>
  <button id='btn-cohort' class='tabbtn'        onclick="showTab('tab-cohort',this)">Carte cohorte</button>
  <button id='btn-rt'     class='tabbtn'        onclick="showTab('tab-rt',this)">Intégrité RT</button>
</nav>
<main>
  <section class='tabpane active' id='tab-overview'>{overview}</section>
  <section class='tabpane' id='tab-cohort'>{cohort_body}</section>
  <section class='tabpane' id='tab-rt'>{rt_tab}</section>
</main>
<script>{DASHBOARD_JS}</script>
</body></html>"""
    out_path = Path(out_path)
    out_path.write_text(page_out, encoding="utf-8")
    return out_path
