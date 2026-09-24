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
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .cohort_export import PRESENCE_COLUMNS, missing_due, patient_timing, presence_long
from .completeness import (
    Protocol,
    completeness_gaps,
    completeness_grid,
    patient_completeness,
)
from .fsutil import atomic_write_text
from .report_completeness import (
    RUO_TEXT,
    completeness_styles,
    followup_detail_html,
    followup_export_text,
    followup_inline_html,
    followup_status_html,
    gap_table_html,
)
from .report_overview import (
    MOD_COLORS,
    cohort_overview,
    overview_script,
    overview_section_html,
    overview_styles,
)
from .rt_integrity import order_for_review

try:
    import plotly.graph_objects as go

    _HAS_PLOTLY = True
except Exception:  # pragma: no cover
    _HAS_PLOTLY = False

LOG = logging.getLogger("DICOM_discovery.report_cohort")

# RUO_TEXT itself lives in report_completeness (this direction of import is cycle-free: that
# module only imports Protocol from .completeness, never anything from this one at module
# level). Two spellings of one regulatory notice — an em dash here, an ASCII hyphen there —
# shipped in the cohort report and the standalone page at once; importing keeps them from
# drifting apart again.

# Verdict colours (semantics fixed by the design spec), at clinical-document saturation:
# each must stay readable as text on --surface (WCAG AA) and never rely on hue alone, which
# is why every verdict also carries its word. NO_RT is a neutral slate: a patient with no
# RT objects is *out of scope*, not a failure.
VERDICT_COLORS = {
    "OK": "#1e7a4f",          # clinical green
    "WARN": "#a36400",        # ochre
    "INCOMPLETE": "#b3261e",  # brick red
    "NO_RT": "#5f7085",       # slate (out of scope)
}
VERDICT_ORDER = ["OK", "WARN", "INCOMPLETE", "NO_RT"]

# Per-modality colours: one palette, owned by report_overview, so the cohort map and the
# overview's modality bars paint CT the same colour. They used to be two dicts that each
# claimed to match the other and did not.
_MOD_COLORS = MOD_COLORS
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
#: The map used to grow 26px per patient, so a 98-patient cohort was a 2 700px plot that no
#: zoom could bring back onto one screen - "zooming out" meant scrolling the page. Height is
#: now capped: the full cohort fits one view, and zooming in is how a reader gets detail.
_MAP_MAX_HEIGHT = 720

MAP_CONFIG = {
    "scrollZoom": True,
    "displayModeBar": True,
    "displaylogo": False,
    "doubleClick": "reset+autosize",
    "modeBarButtonsToAdd": ["zoomIn2d", "zoomOut2d", "autoScale2d"],
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
    "toImageButtonOptions": {"format": "png", "filename": "cohort_map", "scale": 2},
}


def _map_height(n_patients: int) -> int:
    return min(_MAP_MAX_HEIGHT, max(320, 26 * n_patients + 150))


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
            marker=dict(size=size, color=_MOD_COLORS.get(mod, "#6b7785"),
                        symbol="diamond" if is_struct else "circle", opacity=.95,
                        line=dict(color="rgba(255,255,255,.95)", width=1)),
            customdata=custom,
            hovertemplate=("<b>%{y}</b> · %{customdata[0]}"
                           "<br>Date : %{customdata[3]}"
                           "<br>Series : %{customdata[1]} · Studies : %{customdata[2]}"
                           "<br><b>Source</b> :<br>%{customdata[4]}<extra></extra>"),
        ))
    # Light, matching the page: transparent paper so the card shows through, gridlines drawn
    # in the theme's own hairline colour, hover labels on the card surface.
    fig.update_layout(
        template="plotly_white",
        height=_map_height(len(patients)),
        margin=dict(t=24, l=96, r=24, b=56),
        dragmode="zoom",
        xaxis=dict(title="DICOM study date", gridcolor="#e1e8f0", zerolinecolor="#b9c7d6",
                   linecolor="#b9c7d6"),
        yaxis=dict(title="patient", type="category", categoryorder="array",
                   categoryarray=patients, autorange="reversed", gridcolor="#e1e8f0",
                   linecolor="#b9c7d6"),
        legend=dict(orientation="h", y=1.04, x=0, title="", font=dict(color="#243a52")),
        font=dict(color="#243a52", family="Segoe UI, ui-sans-serif, system-ui, sans-serif", size=12),
        hoverlabel=dict(bgcolor="#fbfcfe", bordercolor="#0f6b87", font=dict(color="#10243b")),
        hovermode="closest", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    # The whole cohort must be reachable in one view, and the view must zoom out as well as in:
    # wheel zoom, explicit zoom-in / zoom-out / fit buttons always on the bar, double-click
    # back to the full cohort.
    return fig.to_html(full_html=False, include_plotlyjs=bool(embed_js), config=MAP_CONFIG)


# --------------------------------------------------------------------------- #
# HTML templating
# --------------------------------------------------------------------------- #
def _esc(v) -> str:
    return html.escape(str(v))


def _fmt_study_date(raw: str) -> str:
    """DICOM StudyDate (YYYYMMDD) -> readable YYYY-MM-DD; pass through anything else."""
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw or "undated"


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
    color = VERDICT_COLORS.get(status, "#5f7085")
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
    if manifest.get("n_dirs_unreadable"):
        # A partial scan (permissions / unmounted share) must be visible in the report itself.
        items.insert(1, ("⚠ unreadable dirs — PARTIAL scan", manifest["n_dirs_unreadable"]))
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
        static(pct_str, "mean per-patient completeness"),
    ]
    return f'<section class="kpis">{"".join(cells)}</section>'


def patient_issues(row: dict, fu: Optional[dict]) -> List[str]:
    """Everything wrong with one patient, in words: the RT chain first, then follow-up.

    The Issue box used to show the RT engine's ``reason`` alone, which is empty for every
    patient whose chain is fine - including a patient whose chain is fine but who is missing
    three follow-up MRIs, the case the follow-up column exists to catch. An empty box beside
    a red "3 / 8 missing" badge reads as "nothing to do". Both engines now feed the one list.
    """
    issues = [part.strip() for part in str(row.get("reason", "") or "").split(" ; ")
              if part.strip()]
    if not fu:
        return issues
    cells = fu.get("cells") or []
    if cells and int(fu.get("n_expected", 0) or 0) == 0 and all(
            str(c.get("state")) == "UNMAPPED" for c in cells):
        issues.append("follow-up cannot be graded: no study date falls inside a protocol "
                      "window (check the study dates)")
        return issues
    # The same absence seen by both engines is one issue, not two: "missing RTDOSE" from the
    # RT chain already says what "missing at baseline: RTDOSE" would repeat.
    rt_missing = {m.strip() for i in issues if i.startswith("missing ")
                  for m in i[len("missing "):].split(",")}
    # One line per modality, listing its visits: "MR missing at baseline, M3, M6, M12" is one
    # fact to act on; four "missing at <visit>: MR" lines were the same fact, four times.
    by_mod: Dict[str, List[str]] = {}
    for cell in cells:
        for it in cell.get("items") or []:
            mod = str(it["modality"])
            if it.get("state") == "MISSING" and mod not in rt_missing:
                by_mod.setdefault(mod, []).append(str(cell.get("timepoint")))
    issues.extend(f"{mod} missing at {', '.join(tps)}" for mod, tps in by_mod.items())
    return issues


def _issues_html(issues: List[str]) -> str:
    if not issues:
        return "<span class='noissue'>No issue</span>"
    return "<ul class='issues'>" + "".join(f"<li>{_esc(i)}</li>" for i in issues) + "</ul>"


def _followup_status(fu: Optional[dict]) -> str:
    if not fu:
        return "not_graded"
    cells = fu.get("cells") or []
    n_expected = int(fu.get("n_expected", 0) or 0)
    if cells and n_expected == 0 and all(str(c.get("state")) == "UNMAPPED" for c in cells):
        return "ungradeable"
    if n_expected == 0:
        return "nothing_expected"
    return "incomplete" if int(fu.get("n_missing", 0) or 0) else "complete"


def export_table(rows: List[dict], followup: Dict[str, dict],
                 timepoints: List[str], timing: Optional[Dict[str, dict]] = None,
                 due: Optional[Dict[str, int]] = None) -> Tuple[List[str], List[dict]]:
    """The integrity queue as a tidy table: one row per patient, one variable per column.

    The CSV used to be scraped from what the page displays - glyph strips ("✓CT✓STR—DOSE"),
    a verdict cell that sometimes read "WARN fragmented", follow-up as a sentence, and no
    numeric column at all. None of it could be loaded into pandas or R without hand-parsing.
    This is built from the data instead, so every column has one meaning and one type:

    * identifiers and categories in snake_case (``rt_status``, ``fu_status``);
    * booleans as ``true`` / ``false``;
    * counts as integers, ``fu_pct_complete`` as a float - empty where it is undefined
      (nothing expected, or ungradeable), never a misleading 100;
    * one ``fu_<timepoint>_<modality>`` column per pair the protocol grades, holding
      PRESENT / MISSING / EXTRA / UNMAPPED, empty when nothing is expected there;
    * ``issues`` as the only free-text column, ``;``-separated, with ``n_issues`` beside it;
    * with ``timing``: inclusion and last-exam dates, follow-up length, where the patient id
      came from, undated exams and exams outside every window - and ``fu_n_missing_due``,
      the missing count restricted to visits whose window had closed at extraction, which is
      the one to compute missingness rates on.
    """
    timing = timing or {}
    due = due or {}
    pairs: List[Tuple[str, str]] = []
    for tp in timepoints:
        mods = set()
        for fu in followup.values():
            for cell in fu.get("cells") or []:
                if str(cell.get("timepoint")) == tp:
                    mods.update(str(it["modality"]) for it in cell.get("items") or [])
        pairs.extend((tp, m) for m in sorted(mods))

    columns = ["patient_id", "baseline_date", "last_study_date", "followup_days",
               "patient_id_source", "n_studies_undated", "n_studies_outside_windows",
               "rt_status", "fragmented",
               "has_CT", "has_RTSTRUCT", "has_RTPLAN", "has_RTDOSE",
               "has_GTV", "has_CTV", "has_PTV",
               "n_studies", "n_rt_studies", "n_roi_nonstandard",
               "fu_status", "fu_n_expected", "fu_n_present", "fu_n_missing",
               "fu_n_missing_due", "fu_pct_complete"]
    columns += [f"fu_{tp}_{mod}" for tp, mod in pairs]
    columns += ["n_issues", "issues"]

    records = []
    for r in rows:
        pid = r["patient_id"]
        fu = followup.get(pid)
        status = _followup_status(fu)
        rec = {"patient_id": pid, "rt_status": r["rt_status"], "fragmented": r["fragmented"]}
        t = timing.get(pid, {})
        for key in ("baseline_date", "last_study_date", "followup_days", "patient_id_source",
                    "n_studies_undated", "n_studies_outside_windows"):
            rec[key] = t.get(key)
        for (key, _label), present in zip(_CHAIN, r["chain"]):
            rec[key] = present
        for t in ("GTV", "CTV", "PTV"):
            rec[f"has_{t}"] = r["targets"][t]
        rec.update(n_studies=r["n_studies"], n_rt_studies=r["n_rt_studies"],
                   n_roi_nonstandard=r["n_roi_nonstandard"], fu_status=status)
        graded = status in ("complete", "incomplete")
        rec["fu_n_expected"] = int(fu["n_expected"]) if graded else None
        rec["fu_n_present"] = int(fu["n_present"]) if graded else None
        rec["fu_n_missing"] = int(fu["n_missing"]) if graded else None
        rec["fu_n_missing_due"] = due.get(pid, 0) if graded else None
        rec["fu_pct_complete"] = round(float(fu["pct_complete"]), 1) if graded else None
        states = {}
        for cell in (fu or {}).get("cells") or []:
            for it in cell.get("items") or []:
                states[(str(cell.get("timepoint")), str(it["modality"]))] = str(it["state"])
        for tp, mod in pairs:
            rec[f"fu_{tp}_{mod}"] = states.get((tp, mod))
        issues = patient_issues(r, fu)
        rec["n_issues"] = len(issues)
        rec["issues"] = "; ".join(issues)
        records.append(rec)
    return columns, records


def _export_json(columns: List[str], records: List[dict]) -> str:
    """The export payload, safe to inline in a <script> element."""
    payload = json.dumps({"columns": columns, "records": records}, ensure_ascii=True)
    return payload.replace("</", "<\\/")


def followup_only_patients(rows: List[dict], followup: Dict[str, dict]) -> List[str]:
    """Patients the protocol grades that the RT table has no row for.

    The merged table is keyed on the RT rollup, so anyone the rollup does not list would
    simply not be drawn - and a patient silently absent from a QC registry is the one failure
    mode a QC registry cannot have. In a normal run both views come from one index and this is
    empty; when it is not, the table says so by name rather than quietly shrinking.
    """
    listed = {r["patient_id"] for r in rows}
    return sorted(pid for pid in followup if pid not in listed)


def _rt_table_html(rows: List[dict], findings: Dict[str, List[dict]],
                   followup: Dict[str, dict], timepoints: List[str],
                   timing: Optional[Dict[str, dict]] = None,
                   presence: Optional[List[dict]] = None) -> str:
    """The cohort's one work queue: RT-chain integrity and protocol follow-up on one row.

    These were two tabs. They are two verdicts about the same patient, and splitting them made
    a reader reconcile two tables by patient id to answer one question - "what do I re-export
    for this person?". Folding follow-up in costs one column; the two numeric columns it
    displaces (studies, RT studies) move into the drill-down, where they were being read
    carefully anyway rather than scanned.
    """
    body = []
    for i, r in enumerate(rows):
        pid = r["patient_id"]
        frag = "<span class='tag frag'>fragmented</span>" if r["fragmented"] else ""
        detail = findings.get(pid, [])
        has_detail = bool(detail)
        caret = "▸" if has_detail else ""
        # filter text: everything searchable, lowercased, in a data attribute.
        fu = followup.get(pid)
        fu_text = followup_export_text(fu) if fu else ""
        fu_missing = int((fu or {}).get("n_missing", 0) or 0)
        # The filter text carries the follow-up too, so typing "M3" or "RTDOSE" finds the
        # patients missing it without a second search box over a second table.
        issues = patient_issues(r, fu)
        ftext = " ".join([pid, r["rt_status"], " ".join(issues),
                          "fragmented" if r["fragmented"] else "", fu_text]).lower()
        body.append(
            f"<tr class='prow{' has-detail' if has_detail else ''}' "
            f"data-filter=\"{_esc(ftext)}\" data-row='{i}' "
            f"data-pid='{_esc(pid)}' data-status='{_esc(r['rt_status'])}' "
            f"data-nstudies='{r['n_studies']}' data-nrt='{r['n_rt_studies']}' "
            f"data-missing='{fu_missing}'>"
            f"<td class='c-caret'>{caret}</td>"
            f"<td class='c-pid mono'>{_esc(pid)}</td>"
            f"<td class='c-status'>{_verdict_pill(r['rt_status'])}{frag}</td>"
            f"<td class='c-presence'>{_presence_strip_html(r['chain'], r['targets'])}</td>"
            f"<td class='c-fu' data-export=\"{_esc(fu_text)}\">"
            f"{followup_inline_html(fu, timepoints) if fu else '<span class=dim>—</span>'}"
            f"{('<div class=c-fustat>' + followup_status_html(fu) + '</div>') if fu else ''}</td>"
            f"<td class='c-reason' data-export=\"{_esc('; '.join(issues))}\">"
            f"{_issues_html(issues)}</td>"
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
                date = _fmt_study_date(s["study_date"])
                is_rt = s["rt_status"] != "NOT_RT"
                if is_rt:
                    n = s["n_roi"]
                    structs = f"{n} structure" if n == 1 else f"{n} structures"
                    type_label = f"contains RT · {structs}"
                    if fitems:
                        body_html = f"<ul class='findings'>{''.join(fitems)}</ul>"
                    elif s["rt_status"] == "OK":
                        body_html = "<p class='ok-note'>✓ RT chain complete — no anomalies.</p>"
                    else:
                        body_html = "<p class='dim'>No per-study findings recorded.</p>"
                    study_cls = "study"
                else:
                    # An imaging-only study (follow-up MR/CT): no RT object, nothing to check.
                    type_label = "imaging only · no RT object"
                    body_html = "<p class='dim'>Nothing to check (no RT object).</p>"
                    study_cls = "study study-nort"
                detail_html.append(
                    f"<div class='{study_cls}'><div class='study-head'>"
                    f"<span class='mono study-date'>{_esc(date)}</span>"
                    f"<span class='study-type'>{type_label}</span></div>"
                    f"{body_html}</div>"
                )
            # Drill-down opens with a recap: the verdict, the full data-presence strip, and
            # the recommended action — then the per-study findings beneath it.
            fu_block = ""
            if fu:
                fu_block = (f"<div class='detail-meta wide'><b>Protocol follow-up</b>"
                            f"<span class='dm-body'>{followup_status_html(fu)}"
                            f"{followup_detail_html(fu, timepoints)}</span></div>")
            # Each fact gets its own bordered card, and the border says what kind of fact it
            # is: neutral for description, red for the problem, green for the instruction.
            # Six facts flowed together in one wrapping strip read as one paragraph of labels.
            recap = (
                "<div class='detail-summary'>"
                f"<div class='detail-meta'><b>Verdict</b>"
                f"<span class='dm-body'>{_verdict_pill(r['rt_status'])}{frag}</span></div>"
                f"<div class='detail-meta'><b>Studies</b>"
                f"<span class='dm-body'><span class='dm-big mono'>{r['n_studies']}</span>"
                f"<span class='dim'>of which {r['n_rt_studies']} carry RT objects</span></span></div>"
                f"<div class='detail-meta wide'><b>RT chain</b>"
                f"<span class='dm-body'>"
                f"{_presence_strip_html(r['chain'], r['targets'], detailed=True)}</span></div>"
                f"{fu_block}"
                f"<div class='detail-meta wide {'issue' if issues else 'act'}'><b>Issues</b>"
                f"<span class='dm-body'>"
                f"{_issues_html(issues) if issues else '<span class=noissue>No issue: the RT chain and the protocol follow-up are both complete.</span>'}"
                f"</span></div>"
                "</div>"
            )
            body.append(
                f"<tr class='drow' data-detail-for='{i}' hidden><td colspan='6'>"
                f"<div class='detail'>{recap}<div class='detail-title'>"
                f"Per-study findings — {_esc(pid)}</div>{''.join(detail_html)}</div>"
                "</td></tr>"
            )
    rows_html = "".join(body) or (
        "<tr><td colspan='6' class='dim' style='text-align:center;padding:24px'>"
        "no patients</td></tr>")
    orphans = followup_only_patients(rows, followup)
    orphan_note = ""
    if orphans:
        shown = ", ".join(_esc(p) for p in orphans[:12])
        more = f" +{len(orphans) - 12} more" if len(orphans) > 12 else ""
        orphan_note = (
            f"<p class='orphan-note'><b>{len(orphans)} patient(s) graded against the protocol "
            f"have no row below</b> - the RT index does not list them: "
            f"<span class='mono'>{shown}{more}</span>. Their follow-up is in the gap table "
            f"above; their RT chain was never assessed.</p>")
    presence = presence or []
    columns, records = export_table(rows, followup, timepoints, timing, missing_due(presence))
    return f"""{orphan_note}<script type="application/json" id="rt-data">{_export_json(columns, records)}</script>
<script type="application/json" id="presence-data">{_export_json(PRESENCE_COLUMNS, presence)}</script>
<div class="toolbar">
  <input type="search" id="rt-filter" class="filter" placeholder="Filter patients…"
         aria-label="filter RT table">
  <button type="button" class="btn" id="rt-export"
          title="One row per patient: verdicts, dates, counts">Export patients (CSV)</button>
  <button type="button" class="btn" id="presence-export"
          title="One row per patient x visit x modality, with a 0/1 presence outcome - for statistics">Export presence (CSV)</button>
  <span class="hint">Click a row marked <span class="cue">▸</span> to open its findings.</span>
</div>
<table class="grid" id="rt-table">
  <thead><tr>
    <th class="c-caret" aria-hidden="true"></th>
    <th class="sortable" data-key="pid" data-type="text">Patient</th>
    <th class="sortable" data-key="status" data-type="text">Verdict</th>
    <th>RT chain</th>
    <th class="sortable" data-key="missing" data-type="num">Protocol follow-up</th>
    <th>Issues</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>"""


def _styles() -> str:
    # Design language: "Clinical navy" - the register of hospital imaging software, not of a
    # dashboard. A softly blue-tinted canvas (neither white nor grey), near-white cards, a
    # deep navy header bar, and a single petrol-blue accent used for structure. No glow, no
    # gradient text, no decorative motion: a physician reads this page to decide what to
    # chase, and anything that competes with the four verdict colours costs attention.
    #
    # Two rules keep the sheet honest:
    # * Colour means something. OK / WARN / INCOMPLETE / NO_RT and present / missing keep
    #   their own hues and are never used as decoration.
    # * No colour is mixed against a literal. Every "a hint of this hue" blends into --mix-up
    #   (the card) and every "a readable shade of this hue" into --mix-down (the ink), so the
    #   whole theme turns on the :root block below and nothing else.
    return """
:root{
  color-scheme:light;
  /* Canvas and surfaces: a blue-tinted stack, lifted toward white for what is read. */
  --bg:#e7edf4; --surface:#fbfcfe; --surface-2:#f1f5f9; --surface-3:#e6ecf3;
  --line:#d5dfe9; --line-strong:#b9c7d6;
  /* Ink: navy, not black. */
  --ink:#10243b; --text:#243a52; --muted:#51657c; --dim:#7a8ca0;
  /* One accent, petrol blue; the navy of the header bar is its dark partner. */
  --accent:#0f6b87; --accent-2:#173f66; --navy:#122f4d;
  --accent-soft:#e2eff4; --accent-line:#a8ccd9;
  --grad:linear-gradient(90deg,#0f6b87,#173f66);
  /* Functional hues, at clinical-document saturation. */
  --ok:#1e7a4f; --warn:#a36400; --incomplete:#b3261e; --nort:#5f7085; --absent:#b3261e;
  /* Tint bases - see the rule in _styles(). */
  --mix-up:#fbfcfe; --mix-down:#0b1a2b;
  --glass:linear-gradient(transparent,transparent);
  --shadow:0 1px 2px rgba(16,36,59,.05),0 6px 18px -12px rgba(16,36,59,.18);
  --glow:0 0 0 3px rgba(15,107,135,.18);
  --mono:ui-monospace,"SF Mono","Cascadia Mono",SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --sans:"Segoe UI",ui-sans-serif,system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif;
  --radius:8px; --radius-sm:5px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{background:var(--bg)}
body{
  background:var(--bg);
  color:var(--text);font-family:var(--sans);
  font-size:13.5px;line-height:1.55;-webkit-font-smoothing:antialiased;
  font-feature-settings:"tnum" 1,"cv11" 1;min-height:100vh;
}
::selection{background:#bfdbe6;color:var(--ink)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--line-strong);border-radius:10px;
  border:2px solid var(--bg)}
::-webkit-scrollbar-thumb:hover{background:#9fb2c6}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.dim{color:var(--dim)}

/* ---- topbar: the navy bar of clinical software - identity lives here, not in effects ---- */
.topbar{
  position:sticky;top:0;z-index:30;
  display:flex;align-items:center;gap:18px;flex-wrap:wrap;
  padding:12px 30px;background:var(--navy);color:#e6eef7;
  border-bottom:3px solid var(--accent);
}
.brand{display:flex;align-items:baseline;gap:12px}
.title{font-size:16px;font-weight:700;letter-spacing:-.005em;color:#fff}
.title .thin{font-weight:400;color:#b7c8db}
.subtitle{
  color:#b7c8db;font-size:12px;
  border-left:1px solid rgba(255,255,255,.22);padding-left:12px;align-self:center;
}
/* RUO: a regulatory line, legible on navy, never styled as a badge to be clicked. */
.ruo{
  margin-left:auto;font-size:11px;font-weight:600;letter-spacing:.01em;color:#ffe2a8;
  background:rgba(255,196,64,.10);border:1px solid rgba(255,196,64,.38);
  padding:4px 11px;border-radius:var(--radius-sm);
}
.manifest{
  display:flex;flex-wrap:wrap;gap:6px 30px;width:100%;
  padding-top:10px;margin-top:3px;border-top:1px solid rgba(255,255,255,.14);
}
.manifest-item{display:flex;flex-direction:column;gap:1px;line-height:1.25}
.mk{font-size:10px;color:#9fb3c9;letter-spacing:.08em;text-transform:uppercase}
.mv{
  font-family:var(--mono);font-size:11.5px;color:#e6eef7;
  max-width:54ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}

/* ---- layout ---- */
main{max-width:1240px;margin:0 auto;padding:28px 30px 52px}

/* ---- KPI strip: one glass slab, segments divided by hairlines ---- */
.kpis{
  display:flex;flex-wrap:wrap;align-items:stretch;
  background:var(--glass),var(--surface);border:1px solid var(--line);
  border-radius:var(--radius);box-shadow:var(--shadow);
  margin-bottom:26px;overflow:hidden;
}
.kpi{
  display:flex;flex-direction:column;gap:6px;padding:15px 22px;
  border-right:1px solid var(--line);min-width:118px;
}
.kpi:last-child{border-right:none;margin-left:auto;text-align:right;align-items:flex-end}
.kpi-num{font-size:23px;font-weight:700;color:var(--ink);letter-spacing:-.02em;line-height:1}
.kpi-lab{font-size:11px;color:var(--muted);letter-spacing:.06em;text-transform:uppercase}
.kpi-lab.ok{color:var(--ok)}
.kpi-lab.warn{color:var(--warn)}
.kpi-lab.incomplete{color:var(--incomplete)}
.kpi-lab.nort{color:var(--nort)}
.kpi-lab.act{color:var(--warn);font-weight:650}
/* Clickable filter chips: the count segments narrow the table + map by status. */
.kpi-btn{
  appearance:none;border:0;border-right:1px solid var(--line);
  font:inherit;text-align:left;color:inherit;background:transparent;
  cursor:pointer;transition:background .18s,box-shadow .18s;position:relative;
}
.kpi-btn:hover{background:var(--surface-2)}
.kpi-btn:focus-visible{outline:none;box-shadow:inset 0 0 0 2px var(--accent-line)}
.kpi-btn[aria-pressed="true"]{background:var(--accent-soft)}
.kpi-btn[aria-pressed="true"]::after{content:"";position:absolute;left:14px;right:14px;
  bottom:0;height:3px;border-radius:2px 2px 0 0;background:var(--accent)}
.kpi-static{margin-left:auto;text-align:right;align-items:flex-end}

/* ---- tabs: the active one carries the gradient and a glowing rule ---- */
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:24px}
.tab{
  appearance:none;background:none;border:none;font:inherit;color:var(--muted);
  font-size:13px;font-weight:600;padding:10px 18px 11px;cursor:pointer;position:relative;
  border-radius:var(--radius-sm) var(--radius-sm) 0 0;transition:color .18s,background .18s;
}
.tab:hover{color:var(--ink);background:var(--surface-2)}
.tab[aria-selected="true"]{color:var(--accent-2);background:var(--surface)}
.tab[aria-selected="true"]::after{content:"";position:absolute;left:10px;right:10px;
  bottom:-1px;height:3px;border-radius:2px 2px 0 0;background:var(--accent)}
.tab:focus-visible{outline:none;box-shadow:inset 0 0 0 2px var(--accent-line)}
.tab .count{font-family:var(--mono);font-size:10.5px;color:var(--accent);margin-left:8px;
  background:var(--accent-soft);padding:1px 7px;border-radius:999px}
.panel[hidden]{display:none}

/* ---- toolbar ---- */
.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.filter{
  background:var(--surface);border:1px solid var(--line-strong);color:var(--ink);
  font:inherit;font-size:13px;padding:8px 13px;border-radius:var(--radius-sm);min-width:240px;
  transition:border-color .18s,box-shadow .18s;
}
.filter::placeholder{color:var(--dim)}
.filter:focus-visible{outline:none;border-color:var(--accent);box-shadow:var(--glow)}
.btn{
  background:var(--glass),var(--surface);border:1px solid var(--line-strong);color:var(--text);
  font:inherit;font-size:13px;font-weight:600;padding:8px 15px;border-radius:var(--radius-sm);
  cursor:pointer;transition:border-color .18s,color .18s,box-shadow .18s;
}
.btn:hover{border-color:var(--accent);color:var(--ink);box-shadow:var(--glow)}
.btn:focus-visible{outline:none;box-shadow:var(--glow)}
.hint{color:var(--dim);font-size:12px;margin-left:auto}
.cue{color:var(--accent)}

/* ---- table ---- */
.grid{
  width:100%;border-collapse:collapse;background:var(--glass),var(--surface);
  border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;
  box-shadow:var(--shadow);
}
.grid thead th{
  background:var(--surface-2);text-align:left;
  font-family:var(--sans);font-size:10.5px;font-weight:700;color:var(--muted);
  letter-spacing:.08em;text-transform:uppercase;
  padding:12px 14px;border-bottom:1px solid var(--line-strong);white-space:nowrap;
}
.grid th.num,.grid td.c-num{text-align:right}
.grid th.sortable{cursor:pointer;user-select:none}
.grid th.sortable:hover{color:var(--ink)}
.grid th.sort-asc::after{content:" \\2191";color:var(--accent)}
.grid th.sort-desc::after{content:" \\2193";color:var(--accent)}
.grid td{padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:middle}
.grid tbody tr:last-child td{border-bottom:none}
.grid tbody tr{transition:background .15s}
.grid tbody tr:hover{background:#eef4f9}
.prow.has-detail{cursor:pointer}
.prow.open{background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent)}
.prow.open:hover{background:var(--accent-soft)}
.c-caret{width:22px;color:var(--accent);text-align:center;font-size:11px}
.c-pid{font-family:var(--mono);font-weight:650;color:var(--ink)}
.c-reason{color:var(--text);font-size:12.5px;max-width:34ch}
.issues{margin:0;padding-left:15px;line-height:1.5}
.issues li::marker{color:var(--incomplete)}
.detail-meta .issues{padding-left:17px}
.noissue{color:var(--ok);font-weight:600;font-size:12.5px}

/* ---- verdict tag: a lit pill, the dot glows its own verdict ---- */
.pill{
  display:inline-flex;align-items:center;gap:7px;
  font-size:11px;font-weight:700;letter-spacing:.04em;
  color:color-mix(in srgb,var(--pill) 84%,var(--mix-down));
  background:color-mix(in srgb,var(--pill) 13%,var(--mix-up));
  border:1px solid color-mix(in srgb,var(--pill) 36%,transparent);
  padding:2px 9px 2px 8px;border-radius:var(--radius-sm);white-space:nowrap;
}
.pill::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--pill);
  flex:none}
.tag.frag{
  margin-left:8px;font-size:10.5px;color:var(--warn);
  border:1px solid color-mix(in srgb,var(--warn) 38%,transparent);
  padding:1px 7px;border-radius:999px;
}

/* ---- data-presence strip (CT STR PLAN DOSE / GTV CTV PTV) ---- */
.presence{display:inline-flex;gap:3px;flex-wrap:nowrap}
.pcell{
  display:inline-flex;flex-direction:column;align-items:center;justify-content:center;
  min-width:38px;padding:3px 4px;border-radius:var(--radius-sm);line-height:1.15;
  border:1px solid var(--line-strong);background:var(--surface-2);
}
.pcell .pmark{font-size:11px;font-weight:700;color:var(--muted)}
.pcell .plabel{font-size:8.5px;letter-spacing:.05em;color:var(--muted)}
.pcell.on{background:color-mix(in srgb,var(--ok) 12%,var(--mix-up));
  border-color:color-mix(in srgb,var(--ok) 40%,transparent)}
.pcell.on .pmark{color:var(--ok)}
.pcell.on .plabel{color:color-mix(in srgb,var(--ok) 70%,var(--mix-down))}
.pcell.off{background:transparent;border:1px dashed var(--line-strong)}
.pcell.off .pmark{color:var(--absent)}
.pcell.off .plabel{color:var(--dim)}
/* The fifth cell (GTV) starts the target group - a hair more separation. */
.pcell:nth-child(5){margin-left:8px}
.presence.detailed{gap:5px;flex-wrap:wrap}
.presence.detailed .pcell{min-width:50px;padding:7px 8px}
.presence.detailed .pcell .pmark{font-size:13px}
.presence.detailed .pcell .plabel{font-size:10px}

/* ---- drill-down detail ---- */
.detail{
  background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius);
  margin:4px 0 10px;padding:18px 20px;
}
.detail-summary{
  display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));
  padding-bottom:16px;margin-bottom:16px;border-bottom:1px solid var(--line);
}
/* Each fact is a card with its own border, so six of them read as six things rather than as
   one wrapping paragraph of bold labels. The left edge carries the kind of fact. */
.detail-meta{
  display:flex;flex-direction:column;gap:7px;font-size:12.5px;
  background:var(--glass),var(--surface);border:1px solid var(--line);
  border-left:3px solid var(--line-strong);border-radius:var(--radius-sm);
  padding:10px 13px 12px;
}
.detail-meta.wide{grid-column:1/-1}
.detail-meta.issue{border-left-color:var(--incomplete);
  background:linear-gradient(90deg,color-mix(in srgb,var(--incomplete) 8%,transparent),transparent 60%),var(--surface)}
.detail-meta.act{border-left-color:var(--ok);
  background:linear-gradient(90deg,color-mix(in srgb,var(--ok) 8%,transparent),transparent 60%),var(--surface)}
.detail-meta>b{color:var(--muted);font-weight:700;font-size:10px;letter-spacing:.1em;text-transform:uppercase}
.dm-body{display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;color:var(--text);line-height:1.55}
.detail-meta.wide .dm-body{flex-direction:column;align-items:flex-start;gap:7px}
.dm-big{font-size:18px;font-weight:750;color:var(--ink)}
.detail-title{font-size:10.5px;font-weight:700;color:var(--accent);letter-spacing:.1em;
  text-transform:uppercase;margin-bottom:12px}
.study{padding:11px 0;border-top:1px solid var(--line)}
.study:first-of-type{border-top:none;padding-top:2px}
.study-head{display:flex;align-items:baseline;gap:12px;margin-bottom:6px;font-size:12.5px}
.study-date{font-weight:650;color:var(--ink)}
.study-type{color:var(--muted);font-size:12px}
/* Imaging-only studies (no RT object) are de-emphasised - informational, not actionable. */
.study-nort{opacity:.7}
.study-nort .study-date{color:var(--muted)}
.ok-note{color:var(--ok);font-size:12.5px}
.findings{list-style:none;display:flex;flex-direction:column;gap:7px}
.findings li{display:flex;align-items:baseline;gap:8px;font-size:12.5px}
.badge{font-size:10px;font-weight:700;padding:1px 8px;border-radius:999px;flex:none;letter-spacing:.03em}
.sev-error{color:var(--incomplete);background:color-mix(in srgb,var(--incomplete) 12%,var(--mix-up));
  border:1px solid color-mix(in srgb,var(--incomplete) 34%,transparent)}
.sev-warning{color:var(--warn);background:color-mix(in srgb,var(--warn) 12%,var(--mix-up));
  border:1px solid color-mix(in srgb,var(--warn) 34%,transparent)}
.sev-info{color:var(--accent);background:var(--accent-soft);
  border:1px solid color-mix(in srgb,var(--accent) 30%,transparent)}
.badge.conf{color:var(--dim);border:1px solid var(--line-strong)}
.ftext{color:var(--text)}

/* ---- completeness ---- */
.c-bar{display:flex;align-items:center;gap:10px;min-width:170px}
.bar{flex:1;height:6px;background:var(--surface-3);border-radius:999px;overflow:hidden}
.bar-fill{display:block;height:100%;background:var(--ok)}
.pct{font-family:var(--mono);font-size:11.5px;color:var(--muted);min-width:42px;text-align:right}
.legend{display:flex;flex-wrap:wrap;gap:7px 18px;margin:10px 0 14px;font-size:12px}
.leg{display:inline-flex;align-items:center;gap:6px}
.sw{width:12px;height:12px;border-radius:3px;border:1px solid var(--line-strong);flex:none}
.leg b{font-weight:650;font-size:11.5px}

/* ---- section heading / plot / note ---- */
.section-title{font-size:13.5px;font-weight:650;color:var(--ink);margin:24px 0 12px}
.maphint{margin:0 0 12px;color:var(--muted);font-size:12.5px}
.plot{
  background:var(--glass),var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:12px;margin-bottom:18px;overflow:hidden;box-shadow:var(--shadow);
}
.note{
  color:var(--muted);padding:16px 18px;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--radius);font-size:13px;
}

/* ---- footer ---- */
footer{
  color:var(--dim);font-size:11.5px;text-align:center;
  padding:28px 20px 44px;border-top:1px solid var(--line);margin-top:28px;line-height:1.7;
}
footer b{color:var(--muted)}

/* ---- reduced motion ---- */
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}

/* ---- responsive ---- */
@media (max-width:760px){
  main{padding:16px}
  .topbar{padding:12px 16px}
  .kpi{min-width:50%;border-right:none}
  .kpi:last-child{margin-left:0;text-align:left;align-items:flex-start}
  .hint{display:none}
  .mv{max-width:24ch}
}

/* ---- the cohort gap block: the one thing on this tab that is not per-patient ---- */
.gapblock{background:linear-gradient(135deg,color-mix(in srgb,var(--incomplete) 7%,transparent),transparent 45%),
  var(--glass),var(--surface);
  border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);
  padding:17px 19px 19px;margin-bottom:20px;border-left:3px solid var(--incomplete)}
.gaph{margin:0 0 7px;font-size:17px;font-weight:700;color:var(--incomplete);
  letter-spacing:-.012em;display:flex;align-items:center;gap:10px}
.gaph::before{content:'';width:4px;height:18px;border-radius:2px;background:var(--incomplete)}
.gaplede{margin:0 0 12px;font-size:12.5px;color:var(--muted);line-height:1.65}
.gapblock .gaptable{margin-bottom:0}
/* The follow-up badge sits under its chips: the chips say what is missing, the badge says
   how much, and stacking them keeps the column from growing a second row of text. */
.c-fustat{margin-top:3px}

/* A patient the protocol grades but the RT index never listed must be named, not dropped. */
.orphan-note{background:color-mix(in srgb,var(--warn) 9%,var(--mix-up));
  border:1px solid color-mix(in srgb,var(--warn) 32%,transparent);border-left:3px solid var(--warn);
  border-radius:var(--radius-sm);padding:10px 13px;margin:0 0 14px;font-size:12px;
  color:var(--text);line-height:1.6}
.orphan-note b{color:var(--warn)}
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
        // A cell whose text is a run of glyphs and screen-reader words exports what it
        // MEANS instead, via data-export — otherwise the CSV says "✗MRmissing✓CTpresent".
        var ex = td.getAttribute('data-export');
        cells.push(ex !== null ? ex : td.textContent.replace(/\s+/g,' ').trim());
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
  // The integrity export is built from the tidy payload, not scraped from the display: one
  // row per patient currently shown (the filter applies), one typed variable per column.
  function csvCell(v){
    if(v === null || v === undefined) return '';
    if(v === true) return 'true';
    if(v === false) return 'false';
    var t = String(v);
    return /[",\r\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t;
  }
  function exportTidy(filename, dataId){
    var node = document.getElementById(dataId || 'rt-data'), table = document.getElementById('rt-table');
    if(!node || !table){ exportCsv('rt-table', filename); return; }
    var data = JSON.parse(node.textContent), shown = {};
    table.querySelectorAll('tbody tr.prow').forEach(function(tr){
      if(!tr.hidden) shown[tr.getAttribute('data-pid')] = true;
    });
    var lines = [data.columns.join(',')];
    data.records.forEach(function(rec){
      if(!shown[rec.patient_id]) return;
      lines.push(data.columns.map(function(c){ return csvCell(rec[c]); }).join(','));
    });
    // UTF-8 BOM so Excel reads accents; pandas and R strip it on read.
    var blob = new Blob(['\ufeff' + lines.join('\n') + '\n'], {type:'text/csv;charset=utf-8;'});
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url; a.download = filename; a.rel = 'noopener';
    document.body.appendChild(a); a.click();
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
  }
  var rtExp = document.getElementById('rt-export');
  if(rtExp) rtExp.addEventListener('click', function(){ exportTidy('rt_integrity.csv', 'rt-data'); });
  var prExp = document.getElementById('presence-export');
  if(prExp) prExp.addEventListener('click', function(){ exportTidy('presence_long.csv', 'presence-data'); });

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

    # Completeness no longer has a tab of its own: it is a second verdict about the same
    # patient, so it rides in the integrity row. What survives as its own block is the cohort
    # gap table, which is not per-patient at all — it says which single re-export closes the
    # largest hole, and that question has no patient to hang off.
    comp_grid = completeness_grid(comp_long)
    comp_gaps = completeness_gaps(comp_long)
    followup = {str(p["patient"]): p for p in comp_grid}
    timepoints = list(protocol.timepoints)
    # Analysis-ready exports: patient dates and the long presence table, so a statistician
    # can tell a missing exam from one that was not yet due.
    timing = patient_timing(table, protocol)
    presence = presence_long(table, comp_long, protocol, manifest)

    # The overview opens the report. A reader who lands on a work queue has no way to check
    # that the queue is about the cohort they meant; this tab answers that first, and grades
    # nothing. Its verdict pie draws build_kpis' own counts rather than deriving its own, so
    # the front page and the integrity tab cannot disagree about them.
    overview = cohort_overview(table, manifest, kpis["verdicts"])

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_styles()}{completeness_styles()}{overview_styles()}</style>
</head><body>
{_topbar_html(manifest)}
<main>
  {_kpi_html(kpis)}
  <div class="tabs" role="tablist" aria-label="report sections">
    <button class="tab" role="tab" data-tab="overview" aria-selected="true">
      Overview</button>
    <button class="tab" role="tab" data-tab="rt" aria-selected="false">
      Integrity &amp; follow-up<span class="count">{n_rt}</span></button>
    <button class="tab" role="tab" data-tab="map" aria-selected="false">
      Cohort map</button>
  </div>

  <section class="panel" role="tabpanel" data-panel="overview">
    {overview_section_html(overview)}
  </section>

  <section class="panel" role="tabpanel" data-panel="rt" hidden>
    <section class="gapblock">
      <h3 class="gaph">Biggest gaps in the cohort</h3>
      <p class="gaplede">A QC round does not re-export a patient, it re-exports a timepoint
        and a modality for everyone missing it. Worst first &mdash; the top row is the single
        batch that closes the largest hole.</p>
      {gap_table_html(comp_gaps)}
    </section>
    {_rt_table_html(rt_rows, findings, followup, timepoints, timing, presence)}
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
<script>{_script()}{overview_script()}</script>
</body></html>"""

    atomic_write_text(out, page)
    LOG.info("Cohort report -> %s (%d patients)", out, kpis["n_patients"])
    return str(out)

