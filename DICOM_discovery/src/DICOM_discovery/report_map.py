"""Self-contained completeness heatmap (observed vs. expected).

Renders the :func:`DICOM_discovery.completeness.build_completeness` matrix as a
single HTML file with Plotly **embedded** (no CDN) so it opens on an air-gapped clinical
network. The whole point of the colour encoding is that an *empty-but-expected* cell
(MISSING, red) is visually distinct from an *irrelevant* cell (NA, grey) — a file count
cannot make that distinction.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from .completeness import CellState, Protocol, patient_completeness

try:
    import plotly.graph_objects as go

    _HAS_PLOTLY = True
except Exception:  # pragma: no cover
    _HAS_PLOTLY = False

LOG = logging.getLogger("DICOM_discovery.report_map")

# Discrete colour per CellState (0..4).
_STATE_COLORS = {
    CellState.NA: "#e9ecef",        # grey   — not expected
    CellState.PRESENT: "#2e7d32",   # green  — expected & present
    CellState.MISSING: "#c62828",   # red    — expected & absent (actionable)
    CellState.EXTRA: "#1565c0",     # blue   — present but unexpected
    CellState.UNMAPPED: "#f9a825",  # amber  — data present but no timeline placement
}
_LEGEND = [
    ("PRESENT", "#2e7d32", "expected and found"),
    ("MISSING", "#c62828", "expected but absent"),
    ("EXTRA", "#1565c0", "found, not in protocol"),
    ("UNMAPPED", "#f9a825", "data present, not placed on timeline"),
    ("N/A", "#e9ecef", "not expected"),
]


def _discrete_colorscale():
    """Build a 0..3 banded colorscale matching CellState integers."""
    n = len(_STATE_COLORS)
    scale = []
    for state in sorted(_STATE_COLORS):
        lo = state / n
        hi = (state + 1) / n
        color = _STATE_COLORS[CellState(state)]
        scale.append([lo, color])
        scale.append([hi, color])
    return scale


def render_completeness_map(state_df: pd.DataFrame, hover_df: pd.DataFrame,
                            long_df: pd.DataFrame, out_html: str,
                            protocol: Protocol, title: Optional[str] = None) -> Path:
    """Write the completeness heatmap + per-patient summary to a self-contained HTML."""
    out = Path(out_html)
    out.parent.mkdir(parents=True, exist_ok=True)

    summary = patient_completeness(long_df)
    n_patients = state_df.shape[0]
    n_missing = int((state_df.values == int(CellState.MISSING)).sum())
    overall = round(float(summary["pct_complete"].mean()), 1) if not summary.empty else 0.0
    title = title or f"Cohort completeness — {protocol.name}"

    if not _HAS_PLOTLY:
        out.write_text(
            f"<html><body><h1>{html.escape(title)}</h1>"
            f"<p>plotly not installed — install the 'viz' extra. "
            f"Patients={n_patients}, missing cells={n_missing}.</p></body></html>",
            encoding="utf-8")
        LOG.warning("plotly absent: wrote minimal fallback to %s", out)
        return out

    fig = go.Figure(go.Heatmap(
        z=state_df.values,
        x=list(state_df.columns),
        y=list(state_df.index),
        text=hover_df.values,
        hoverinfo="text",
        colorscale=_discrete_colorscale(),
        zmin=0, zmax=len(_STATE_COLORS),
        showscale=False,
        xgap=2, ygap=2,
    ))
    fig.update_layout(
        title=title,
        margin=dict(t=60, l=90, r=20, b=120),
        xaxis=dict(tickangle=-45, side="top"),
        yaxis=dict(autorange="reversed", title="patient"),
        height=max(320, 40 * n_patients + 200),
    )
    graph_html = fig.to_html(full_html=False, include_plotlyjs=True)  # embedded => no CDN

    legend = "".join(
        f"<span style='display:inline-block;margin-right:14px'>"
        f"<span style='display:inline-block;width:12px;height:12px;background:{c};"
        f"border:1px solid #999;vertical-align:middle'></span> "
        f"<b>{lab}</b> — {desc}</span>"
        for lab, c, desc in _LEGEND
    )
    rows = "".join(
        f"<tr><td>{html.escape(str(r['patient']))}</td><td>{int(r['n_expected'])}</td>"
        f"<td>{int(r['n_present'])}</td><td>{int(r['n_missing'])}</td>"
        f"<td>{r['pct_complete']}%</td></tr>"
        for _, r in summary.iterrows()
    )
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
 body{{font-family:system-ui,Arial,sans-serif;margin:20px;color:#222}}
 .kpis{{display:flex;gap:24px;margin:8px 0 4px}}
 .kpi{{background:#f5f5f5;border-radius:8px;padding:10px 16px}}
 .kpi b{{font-size:22px;display:block}}
 table{{border-collapse:collapse;margin-top:10px}}
 td,th{{border:1px solid #ccc;padding:4px 10px;font-size:13px;text-align:right}}
 td:first-child,th:first-child{{text-align:left}}
 .legend{{margin:10px 0;font-size:13px}}
</style></head>
<body>
<h1>{html.escape(title)}</h1>
<div class="kpis">
  <div class="kpi"><b>{n_patients}</b>patients</div>
  <div class="kpi"><b>{overall}%</b>mean completeness</div>
  <div class="kpi"><b>{n_missing}</b>missing (expected) cells</div>
</div>
<div class="legend">{legend}</div>
{graph_html}
<h2>Per-patient completeness</h2>
<table><tr><th>patient</th><th>expected</th><th>present</th><th>missing</th><th>%</th></tr>{rows}</table>
<p style="color:#777;font-size:12px;margin-top:18px">Research Use Only — synthetic data; not a clinical safety check.</p>
</body></html>"""
    out.write_text(page, encoding="utf-8")
    LOG.info("Completeness map -> %s", out)
    return out
