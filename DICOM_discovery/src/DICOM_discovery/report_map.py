"""Standalone completeness page (observed vs. expected) — a frame around the shared grid.

Until v0.11 this module rendered a Plotly heatmap of patients x (timepoint x modality). It
was replaced because it did not answer the only question the page exists for — *which
patient do I chase next?* Two thirds of its cells were grey "not expected" filler, the rows
were alphabetical rather than worst-first, state was carried by colour alone, and the file
weighed ~4.9 MB, which is why nobody opened it next to the cohort report.

This module now writes the *same* section that the cohort report's Completeness tab shows
(:func:`DICOM_discovery.report_completeness.completeness_section_html`) — one renderer, two
frames. The page carries no chart library at all: it is a few KB of HTML, CSS and vanilla
JS, so it still opens by double-click on an air-gapped clinical workstation.

The public signature of :func:`render_completeness_map` is unchanged; ``state_df`` and
``hover_df`` are the heatmap-shaped views that the CLI still computes and are accepted (and
ignored) so callers do not have to change.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from .completeness import Protocol, completeness_grid, completeness_kpis
from .fsutil import atomic_write_text
from .report_completeness import completeness_page_html

LOG = logging.getLogger("DICOM_discovery.report_map")


def render_completeness_map(state_df: pd.DataFrame, hover_df: pd.DataFrame,
                            long_df: pd.DataFrame, out_html: str,
                            protocol: Protocol, title: Optional[str] = None) -> Path:
    """Write the completeness grid to a self-contained HTML page and return its path.

    ``state_df`` / ``hover_df`` are part of the historic signature (the CLI still builds
    them) and are deliberately unused: the grid is derived from ``long_df`` through the
    shared data layer so the page can never disagree with the cohort report's tab.
    """
    out = Path(out_html)
    out.parent.mkdir(parents=True, exist_ok=True)

    grid = completeness_grid(long_df)
    kpis = completeness_kpis(long_df)
    page = completeness_page_html(grid, kpis, protocol, title=title)

    atomic_write_text(out, page)
    LOG.info("Completeness page -> %s (%d patients, %d incomplete)",
             out, kpis["n_patients"], kpis["n_incomplete"])
    return out
