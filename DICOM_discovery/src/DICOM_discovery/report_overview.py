"""Cohort overview — what this tool is, and what this cohort is, before any verdict.

The report used to open on a work queue: rows of patients, each with a verdict. That page
answers "which patient is broken?" for someone who already knows what the cohort is, and
nothing at all for someone opening the file for the first time — who does not yet know what
the tool even checks, how many patients there are, over what period, or from which folder,
and therefore cannot judge whether the verdicts below are about the data they think they are.

This module builds that missing first page. It is deliberately split in two halves:

* :func:`cohort_overview` computes numbers from the canonical index and the run manifest and
  returns plain data — no HTML, no pandas in the result — so every figure on the page can be
  asserted in a test;
* :func:`overview_section_html` renders them as charts.

Two rules govern what goes on this page.

**Every number is labelled as the thing it actually counts.** The index collapses an image
series to one row, so "rows" and "files" are different facts by roughly the mean slice count.
They appear under two names, never one.

**Nothing here is a verdict.** The integrity tab grades the cohort. This tab describes it. A
reader must be able to check that the description matches what they expected *before* being
told what is wrong with it.

Every chart is inline SVG or CSS animating plain divs. Nothing is fetched, no charting
library is loaded, and the page opens by double-click on an air-gapped machine — the same
constraint that governs the rest of the report. Every animation is behind
``prefers-reduced-motion``, and every chart states its own numbers in text, so the page is
complete before a single frame has run.
"""
from __future__ import annotations

import datetime
from typing import List, Optional, Tuple

import pandas as pd

#: Modalities in the order a radiotherapy chain is acquired, so a chart reads as a workflow
#: rather than as an alphabet. Anything unlisted sorts after, alphabetically.
_MOD_ORDER = {"CT": 0, "MR": 1, "PT": 2, "RTSTRUCT": 3, "RTPLAN": 4, "RTDOSE": 5, "REG": 6}

#: One hue per modality - THE palette: report_cohort imports it for the cohort map, so a
#: reader who learns CT is blue on the overview finds it blue on the map. Chosen to stay clear
#: of the four verdict hues (green, ochre, brick, slate), so a modality is never mistaken
#: for a verdict on a page that shows both.
MOD_COLORS = {
    "CT": "#2f6fb0", "MR": "#6a4fa3", "PT": "#a8457a",
    "RTSTRUCT": "#c0681a", "RTPLAN": "#1f8585", "RTDOSE": "#7a6a1c",
    "REG": "#56778f", "SEG": "#6f8a2a", "SR": "#8a97a6", "OT": "#6b7785",
}
_MOD_COLOR = MOD_COLORS
_MOD_FALLBACK = "#64748b"

#: Above this many buckets a monthly histogram stops being readable and becomes a comb.
_MAX_TIME_BUCKETS = 44


def _parse_date(raw) -> Optional[datetime.date]:
    """DICOM DA (YYYYMMDD), or None. Same leniency as completeness._parse_date on purpose:
    two modules disagreeing on what counts as a readable date would put two different
    "undated studies" numbers on one page."""
    s = str(raw or "").strip()
    if len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return datetime.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _nunique_nonempty(series: pd.Series) -> int:
    """Distinct non-empty values. A missing UID is one absent fact, not one shared identity —
    counting "" as a value would merge every object that lacks the tag into a single phantom
    study or series."""
    s = series.astype(str).str.strip()
    return int(s[s != ""].nunique())


def _modality_rows(table: pd.DataFrame) -> List[dict]:
    """One row per modality: files, series, studies and patients it appears in.

    Four counts rather than one because they answer four different questions. 4 800 CT *files*
    across 12 *series* in 12 *patients* is a normal cohort; 4 800 files in 1 series is a
    mis-grouped one, and only the pair of numbers can tell them apart.
    """
    rows = []
    for mod, sub in table.groupby(table["modality"].astype(str)):
        if not mod:
            continue
        rows.append({
            "modality": mod,
            "n_files": int(sub["n_instances"].sum()),
            "n_series": _nunique_nonempty(sub["series_uid"]),
            "n_studies": _nunique_nonempty(sub["study_uid"]),
            "n_patients": _nunique_nonempty(sub["patient_id"]),
        })
    rows.sort(key=lambda r: (_MOD_ORDER.get(r["modality"], 99), r["modality"]))
    return rows


def _time_buckets(table: pd.DataFrame) -> Tuple[List[dict], str]:
    """Studies per calendar bucket, for the acquisition histogram.

    Monthly while that stays readable, quarterly beyond — a five-year cohort drawn by month is
    sixty bars two pixels wide, which shows a shape nobody can read off. Empty buckets are
    emitted explicitly: a gap in recruitment is a fact about the cohort, and dropping the
    empty months would draw it as continuous activity.
    """
    df = table.copy()
    df["_date"] = df["study_date"].map(_parse_date)
    dated = df[df["_date"].notna()]
    if dated.empty:
        return [], "month"

    per_study = dated.groupby("study_uid")["_date"].min()
    first, last = per_study.min(), per_study.max()
    n_months = (last.year - first.year) * 12 + (last.month - first.month) + 1
    unit = "month" if n_months <= _MAX_TIME_BUCKETS else "quarter"

    def key(d: datetime.date) -> Tuple[int, int]:
        return (d.year, d.month) if unit == "month" else (d.year, (d.month - 1) // 3 * 3 + 1)

    counts: dict = {}
    for d in per_study:
        counts[key(d)] = counts.get(key(d), 0) + 1

    buckets, cur = [], key(first)
    end = key(last)
    while cur <= end:
        year, month = cur
        label = (f"{year}-{month:02d}" if unit == "month"
                 else f"{year} Q{(month - 1) // 3 + 1}")
        buckets.append({"label": label, "n_studies": int(counts.get(cur, 0))})
        step = 1 if unit == "month" else 3
        month += step
        while month > 12:
            month -= 12
            year += 1
        cur = (year, month)
    return buckets, unit


def _per_patient_time(table: pd.DataFrame) -> dict:
    """Shape of the cohort in time: studies per patient, and how long each is followed.

    Both are computed over *dated* studies only. A patient whose dates are unreadable has no
    measurable follow-up span, and folding them in as a span of zero would drag the median
    toward "no follow-up" for a reason that has nothing to do with follow-up.
    """
    df = table.copy()
    df["_date"] = df["study_date"].map(_parse_date)
    dated = df[df["_date"].notna()]

    per_patient = (dated.groupby("patient_id")
                        .agg(n_studies=("study_uid", "nunique"),
                             first=("_date", "min"), last=("_date", "max")))
    if per_patient.empty:
        return {"studies_per_patient": None, "n_single_study_patients": 0,
                "followup_days": None, "n_patients_dated": 0, "studies_per_patient_hist": []}

    spans = [(row["last"] - row["first"]).days for _, row in per_patient.iterrows()
             if row["n_studies"] > 1]
    counts = per_patient["n_studies"]
    hist = [{"n_studies": int(k), "n_patients": int(v)}
            for k, v in sorted(counts.value_counts().items())]
    return {
        "studies_per_patient": {"min": int(counts.min()),
                                "median": float(counts.median()),
                                "max": int(counts.max())},
        "studies_per_patient_hist": hist,
        # A patient with one study cannot be incomplete on follow-up — there is no follow-up
        # to be missing. Counting them as "incomplete" elsewhere without saying how many they
        # are is how a recent inclusion gets chased for data that does not exist yet.
        "n_single_study_patients": int((counts == 1).sum()),
        "followup_days": ({"median": float(pd.Series(spans).median()), "max": int(max(spans))}
                          if spans else None),
        "n_patients_dated": int(len(per_patient)),
    }


def cohort_overview(table: Optional[pd.DataFrame], manifest: Optional[dict] = None,
                    verdicts: Optional[dict] = None) -> dict:
    """Describe the cohort as indexed. Pure data in, plain data out.

    ``verdicts`` carries the RT counts the integrity tab already computed — borrowed rather
    than recomputed, so the front page and the tab behind it cannot disagree about how many
    patients hold which verdict.
    """
    manifest = manifest or {}
    if table is None or getattr(table, "empty", True):
        return {"empty": True, "root": str(manifest.get("root", "") or ""),
                "generated_utc": str(manifest.get("generated_utc", "") or "")}

    dates = table["study_date"].map(_parse_date)
    dated_studies = table.loc[dates.notna(), "study_uid"]
    undated_studies = table.loc[dates.isna(), "study_uid"]
    buckets, unit = _time_buckets(table)

    ov = {
        "empty": False,
        "root": str(manifest.get("root", "") or ""),
        "generated_utc": str(manifest.get("generated_utc", "") or ""),
        "n_patients": _nunique_nonempty(table["patient_id"]),
        "n_studies": _nunique_nonempty(table["study_uid"]),
        "n_series": _nunique_nonempty(table["series_uid"]),
        "n_files": int(table["n_instances"].sum()),
        "n_rows": int(len(table)),
        "modalities": _modality_rows(table),
        "date_min": min(d for d in dates if d is not None).isoformat() if dates.notna().any() else None,
        "date_max": max(d for d in dates if d is not None).isoformat() if dates.notna().any() else None,
        "time_buckets": buckets,
        "time_bucket_unit": unit,
        # Studies with no readable date: the single most common reason a complete patient is
        # graded ungradeable, so it belongs on the front page and not in a log line.
        "n_undated_studies": _nunique_nonempty(undated_studies) if len(undated_studies) else 0,
        "n_dated_studies": _nunique_nonempty(dated_studies) if len(dated_studies) else 0,
        "n_files_seen": int(manifest.get("n_files_seen", 0) or 0),
        "n_unreadable": int(manifest.get("n_unreadable", 0) or 0),
        "n_dirs_unreadable": int(manifest.get("n_dirs_unreadable", 0) or 0),
        "n_dirs_excluded": int(manifest.get("n_dirs_excluded", 0) or 0),
        # Straight from build_kpis: the overview draws the integrity tab's own counts, it
        # does not compute a second opinion about them.
        "verdicts": {str(k): int(v) for k, v in (verdicts or {}).items() if int(v) >= 0},
    }
    ov.update(_per_patient_time(table))
    return ov


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _esc(v) -> str:
    return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _num(n) -> str:
    """Thousands-separated with a thin space: 4 812, not 4812 or 4,812 — unambiguous for a
    reader whose locale uses the comma as a decimal mark."""
    return f"{int(n):,}".replace(",", " ")


def _stat(value: str, label: str, note: str = "", cls: str = "",
          count: Optional[int] = None) -> str:
    """One figure, its name, and one sentence on what it tells you.

    ``count`` opts the figure into the count-up animation: the script reads it, the markup
    already contains the final text, so a reader with JS disabled or motion reduced sees the
    finished number and never a zero.
    """
    cls = f"ovstat {cls}".strip()
    attr = f" data-count='{int(count)}'" if count is not None else ""
    note_html = f"<p class='ovnote'>{_esc(note)}</p>" if note else ""
    return (f"<div class='{cls}'><div class='ovnum'{attr}>{value}</div>"
            f"<div class='ovlab'>{_esc(label)}</div>{note_html}</div>")


def _block(title: str, lede: str, body: str, cls: str = "") -> str:
    """One card. The title sits in its own span so the gradient can be clipped to the words:
    painted on the heading box itself, a flex heading spans the whole card and the words
    would only ever show the gradient's first, cyan, few percent."""
    klass = f"ovblock {cls}".strip()
    return (f"<section class='{klass}'><h3 class='ovh'><span class='ovh-t'>{_esc(title)}"
            f"</span></h3><p class='ovlede'>{_esc(lede)}</p>{body}</section>")


# --------------------------------------------------------------------------- #
# Charts — inline SVG / animated CSS, no library, no fetch
# --------------------------------------------------------------------------- #
def _bar_chart(items: List[dict], label_key: str, value_key: str,
               color_of, caption: str) -> str:
    """Horizontal bars, each growing from zero on reveal, staggered by index.

    Every bar prints its own value beside it. The chart is the fast read; the number is the
    one a reader quotes, and it must not depend on measuring a bar against an axis.
    """
    if not items:
        return "<p class='ovempty'>Nothing to chart here.</p>"
    top = max(int(i[value_key]) for i in items) or 1
    bars = []
    for i, item in enumerate(items):
        val = int(item[value_key])
        pct = max(1.0, 100.0 * val / top)     # a nonzero count always draws a visible sliver
        bars.append(
            f"<div class='ovbar' style='--i:{i}'>"
            f"<span class='ovbar-lab'>{_esc(item[label_key])}</span>"
            f"<span class='ovbar-track'><span class='ovbar-fill' "
            f"style='--pct:{pct:.1f}%;--hue:{color_of(item)}'></span></span>"
            f"<span class='ovbar-val mono'>{_num(val)}</span></div>"
        )
    return (f"<div class='ovchart ovbars' role='img' aria-label='{_esc(caption)}'>"
            f"{''.join(bars)}</div>")


def _column_chart(buckets: List[dict], unit: str) -> str:
    """Studies per month (or quarter) as columns rising from the axis.

    Only the first, last and tallest bucket are labelled: a 40-column axis with every tick
    written out is unreadable, and every column carries its exact count in its tooltip.
    """
    if not buckets:
        return ("<p class='ovempty'>No study carries a readable date, so there is no "
                "acquisition history to draw.</p>")
    top = max(b["n_studies"] for b in buckets) or 1
    peak = max(range(len(buckets)), key=lambda i: buckets[i]["n_studies"])
    cols = []
    for i, b in enumerate(buckets):
        h = max(2.0, 100.0 * b["n_studies"] / top)
        show = i in (0, len(buckets) - 1, peak)
        cols.append(
            f"<span class='ovcol{' tall' if i == peak else ''}' style='--i:{i};--h:{h:.1f}%' "
            f"title='{_esc(b['label'])} - {b['n_studies']} studies'>"
            f"<span class='ovcol-fill'></span>"
            f"<span class='ovcol-lab{'' if show else ' off'}'>{_esc(b['label'])}</span></span>"
        )
    return (f"<div class='ovchart ovcols' role='img' "
            f"aria-label='studies per {_esc(unit)}, {len(buckets)} buckets, peak "
            f"{buckets[peak]['n_studies']} in {_esc(buckets[peak]['label'])}'>"
            f"{''.join(cols)}</div>")


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #
_FEATURES = [
    ("Indexes any DICOM tree",
     "Walks a folder, a share or a NAS mount and reads headers only - never pixel data. "
     "Nothing is moved, renamed or modified."),
    ("Grades the radiotherapy chain",
     "CT or MR to structures to plan to dose, all in one frame of reference, with structure "
     "names checked against the TG-263 convention. One verdict per patient."),
    ("Grades longitudinal follow-up",
     "Places each study on a protocol timeline from its own StudyDate, then says which "
     "timepoint and modality the cohort is missing, and for whom."),
    ("Delivers one report file",
     "Everything is in this single file: double-click to open it on any hospital computer, "
     "with no installation and no internet connection. Tables can be exported to CSV and "
     "opened in Excel."),
]


def _intro_block(ov: dict) -> str:
    cards = "".join(
        f"<li class='ovfeat' style='--i:{i}'><em class='ovfeat-k mono'>{i + 1:02d}</em>"
        f"<b>{_esc(t)}</b><span>{_esc(d)}</span></li>"
        for i, (t, d) in enumerate(_FEATURES)
    )
    src = ""
    if ov.get("root"):
        gen = f" &middot; indexed {_esc(ov['generated_utc'])}" if ov.get("generated_utc") else ""
        src = (f"<p class='ovsource'><span class='ovsrc-k'>source</span>"
               f"<span class='mono'>{_esc(ov['root'])}</span>{gen}</p>")
    return _block(
        "What DICOM_discovery does",
        "Cohort-level quality control for longitudinal radiotherapy data. It reads what is "
        "already on disk and tells you what is missing, inconsistent or ungradeable - before "
        "a study is built on it. Research Use Only, not a medical device, and never a "
        "clinical safety check.",
        f"<ul class='ovfeats'>{cards}</ul>{src}",
        cls="ovintro",
    )


def _scanned_block(ov: dict) -> str:
    missed = ov["n_unreadable"] + ov["n_dirs_unreadable"]
    stats = [
        _stat(_num(ov["n_files_seen"]), "files walked", count=ov["n_files_seen"],
              note="Every file the scan opened a directory entry for, DICOM or not."),
        _stat(_num(ov["n_files"]), "DICOM files indexed", count=ov["n_files"],
              note="Of those, the ones that are DICOM and were read - headers only."),
        _stat(_num(ov["n_rows"]), "series and RT objects", count=ov["n_rows"],
              note="The rows this report works on: an image series counts once however many "
                   "slices it holds."),
        _stat(_num(missed), "not read", cls="warn" if missed else "", count=missed,
              note="Files that could not be read plus directories that could not be listed. "
                   "A clean cohort with unreadable corners is not a clean cohort."),
    ]
    return _block(
        "What was scanned",
        "Before any verdict: which tree was read, and how much of it came back.",
        f"<div class='ovstats'>{''.join(stats)}</div>",
    )


def _cohort_block(ov: dict) -> str:
    stats = [
        _stat(_num(ov["n_patients"]), "patients", count=ov["n_patients"]),
        _stat(_num(ov["n_studies"]), "studies", count=ov["n_studies"]),
        _stat(_num(ov["n_series"]), "series", count=ov["n_series"]),
        _stat(_num(ov["n_files"]), "files", count=ov["n_files"]),
    ]
    chart = _bar_chart(
        ov["modalities"], "modality", "n_files",
        lambda m: _MOD_COLOR.get(m["modality"], _MOD_FALLBACK),
        "DICOM files per modality",
    )
    rows = "".join(
        f"<tr><td class='ovmod'>{_esc(m['modality'])}</td>"
        f"<td class='ovn mono'>{_num(m['n_files'])}</td>"
        f"<td class='ovn mono'>{_num(m['n_series'])}</td>"
        f"<td class='ovn mono'>{_num(m['n_studies'])}</td>"
        f"<td class='ovn mono'>{_num(m['n_patients'])}</td></tr>"
        for m in ov["modalities"]
    )
    table = (f"<details class='ovdetails'><summary>Exact counts per modality</summary>"
             f"<table class='grid ovtable'><thead><tr><th>Modality</th>"
             f"<th class='ovn'>Files</th><th class='ovn'>Series</th>"
             f"<th class='ovn'>Studies</th><th class='ovn'>Patients</th></tr></thead>"
             f"<tbody>{rows}</tbody></table></details>")
    return _block(
        "What the cohort holds",
        "The bars are files per modality. Files, series, studies and patients are four "
        "different counts: 8 000 CT files across 40 series in 40 patients is an ordinary "
        "cohort, while 8 000 files in one series is a grouping mistake.",
        f"<div class='ovstats ovstats-4'>{''.join(stats)}</div>{chart}{table}",
    )


def _time_block(ov: dict) -> str:
    span = ov.get("date_min") and ov.get("date_max")
    head = (f"<p class='ovspan'><span class='mono'>{_esc(ov['date_min'])}</span>"
            f"<span class='ovspan-line'></span>"
            f"<span class='mono'>{_esc(ov['date_max'])}</span></p>") if span else ""
    chart = _column_chart(ov.get("time_buckets") or [], ov.get("time_bucket_unit", "month"))

    stats = []
    spp = ov.get("studies_per_patient")
    if spp:
        stats.append(_stat(
            f"{spp['min']} <span class='ovto'>/</span> {spp['median']:g} "
            f"<span class='ovto'>/</span> {spp['max']}",
            "studies per patient (min / median / max)",
            note="A wide spread usually means the cohort mixes patients at different stages "
                 "of follow-up, not that data is missing."))
    fu = ov.get("followup_days")
    if fu:
        stats.append(_stat(
            f"{_num(fu['median'])} <span class='ovto'>d</span>", "median follow-up",
            count=int(fu["median"]),
            note="Days between a patient's first and last dated study, over patients with "
                 f"more than one. Longest here: {_num(fu['max'])} days."))
    n_single = ov.get("n_single_study_patients", 0)
    if n_single:
        stats.append(_stat(
            _num(n_single), "patients with a single study", count=n_single,
            note="These have no follow-up to be missing. Chasing them for later timepoints "
                 "asks for data that does not exist yet."))
    n_undated = ov.get("n_undated_studies", 0)
    if n_undated:
        stats.append(_stat(
            _num(n_undated), "studies with no readable date", cls="warn", count=n_undated,
            note="No StudyDate, or one that will not parse. These cannot be placed on any "
                 "protocol timeline."))

    return _block(
        "The cohort in time",
        "A longitudinal protocol is a claim about dates. This is the acquisition history it "
        "has to work with - one column per "
        f"{ov.get('time_bucket_unit', 'month')}, hover a column for its exact count.",
        f"{head}{chart}<div class='ovstats'>{''.join(stats)}</div>",
    )


#: Pie geometry, in a 240-unit box. Wedges are drawn as the stroke of a circle of radius
#: R/2 and width R - the one SVG shape whose dash length maps linearly onto a share of the
#: disc, which is what lets a wedge be *swept* in rather than faded in.
_PIE_BOX = 240
_PIE_C = 120.0            # centre
_PIE_R = 82.0             # outer radius of the wedges
_PIE_HUB = 29.0           # the glass hub that carries the total
_PIE_POP = 4.0            # how far a wedge lifts out when hovered
_PIE_SWEEP_S = 0.9        # time to draw the whole disc, clockwise, in seconds
_PIE_SWEEP_T0 = 0.25      # when drawing starts, once the card is in place

#: What each RT verdict means in one line, so a slice is never just a colour with a code.
_VERDICT_NOTE = {
    "OK": "the RT chain holds together",
    "WARN": "usable, but something needs a look",
    "INCOMPLETE": "a link of the chain is missing",
    "NO_RT": "no RT object at all - out of scope, not a failure",
}


def _pie_point(turn: float, radius: float) -> Tuple[float, float]:
    """A point on the dial, ``turn`` of a full circle clockwise from three o'clock.

    Three o'clock, not twelve: the whole dial group is rotated a quarter turn back in the
    markup, so every coordinate here lives in that rotated frame and twelve o'clock falls out
    of the rotation instead of being corrected for in every formula.
    """
    import math
    rad = 2 * math.pi * turn
    return (_PIE_C + radius * math.cos(rad), _PIE_C + radius * math.sin(rad))


def _verdict_pie_block(ov: dict) -> str:
    """The RT verdict split as a pie with a central total, under what was scanned.

    Deliberately a pie and not a fourth row of counts: the question it answers is "how much of
    this cohort is usable?", which is a proportion. The counts sit beside it in the legend,
    because a proportion is what you see and a count is what you quote.

    The only motion is the disc drawing itself once, clockwise, each wedge in turn - enough
    to show that the parts add up to the whole, and nothing that keeps moving afterwards.
    Everything that animates has its final geometry as its resting style, and the animation
    only supplies the starting frame - with motion reduced or CSS animations unsupported,
    the pie is simply there, complete and correct.
    """
    import math

    from .report_cohort import VERDICT_COLORS, VERDICT_ORDER  # local: report_cohort imports us

    verdicts = ov.get("verdicts") or {}
    total = sum(int(verdicts.get(v, 0)) for v in VERDICT_ORDER)
    if not total:
        return ""

    r = _PIE_R / 2
    circ = 2 * math.pi * r
    present = [(v, int(verdicts.get(v, 0))) for v in VERDICT_ORDER if int(verdicts.get(v, 0)) > 0]

    defs, wedges, seps, legend = [], [], [], []
    start = 0.0
    for i, (name, n) in enumerate(present):
        share = n / total
        color = VERDICT_COLORS[name]
        mid = start + share / 2
        dx = _PIE_POP * math.cos(2 * math.pi * mid)
        dy = _PIE_POP * math.sin(2 * math.pi * mid)
        pct = round(100 * share)
        # Flat hue, a hair lighter toward the hub so adjacent wedges of similar value still
        # separate - print-safe, and nothing that reads as a lighting effect.
        defs.append(
            f"<radialGradient id='pg-{name}' cx='{_PIE_C}' cy='{_PIE_C}' r='{_PIE_R}' "
            f"gradientUnits='userSpaceOnUse'>"
            f"<stop offset='0' stop-color='{color}' stop-opacity='.82'/>"
            f"<stop offset='.55' stop-color='{color}' stop-opacity='.94'/>"
            f"<stop offset='1' stop-color='{color}' stop-opacity='1'/></radialGradient>")
        wedges.append(
            f"<circle class='pw' data-v='{_esc(name)}' cx='{_PIE_C}' cy='{_PIE_C}' r='{r:.3f}' "
            f"fill='none' stroke='url(#pg-{name})' stroke-width='{_PIE_R}' "
            f"style='--len:{share * circ:.3f};--circ:{circ:.3f};--off:{-start * circ:.3f};"
            f"--dx:{dx:.2f}px;--dy:{dy:.2f}px;--c:{color};"
            f"--t0:{_PIE_SWEEP_T0 + start * _PIE_SWEEP_S:.3f}s;"
            f"--d:{max(share * _PIE_SWEEP_S, .05):.3f}s'>"
            f"<title>{_esc(name)}: {n} of {total} patients ({pct}%)</title></circle>")
        if len(present) > 1:
            x, y = _pie_point(start, _PIE_R + .5)
            seps.append(f"<line class='psep' x1='{_PIE_C}' y1='{_PIE_C}' "
                        f"x2='{x:.2f}' y2='{y:.2f}'/>")
        legend.append(
            f"<li class='pl' data-v='{_esc(name)}' tabindex='0' "
            f"style='--c:{color};--pct:{max(share * 100, 1.5):.1f}%;--i:{i}'>"
            f"<span class='pl-dot'></span>"
            f"<span class='pl-name'>{_esc(name)}</span>"
            f"<span class='pl-n mono'>{_num(n)}</span>"
            f"<span class='pl-pct mono'>{pct}%</span>"
            f"<span class='pl-note'>{_esc(_VERDICT_NOTE.get(name, ''))}</span>"
            f"<span class='pl-bar'><i></i></span></li>")
        start += share

    svg = (
        f"<svg class='ovpie' viewBox='0 0 {_PIE_BOX} {_PIE_BOX}' role='img' "
        f"aria-label='RT verdict split over {total} patients' "
        f"style='--sweep:{_PIE_SWEEP_S}s;--t0:{_PIE_SWEEP_T0}s'>"
        f"<defs>{''.join(defs)}</defs>"
        f"<g class='pdial' transform='rotate(-90 {_PIE_C} {_PIE_C})'>"
        f"<circle class='pdisc' cx='{_PIE_C}' cy='{_PIE_C}' r='{_PIE_R}'/>"
        f"{''.join(wedges)}{''.join(seps)}</g>"
        f"<circle class='phub' cx='{_PIE_C}' cy='{_PIE_C}' r='{_PIE_HUB}'/>"
        f"<text class='ptotal' x='{_PIE_C}' y='{_PIE_C + 3}' data-count='{total}'>{_num(total)}</text>"
        f"<text class='psub' x='{_PIE_C}' y='{_PIE_C + 16}'>patients</text>"
        "</svg>"
    )

    return _block(
        "RT verdicts across the cohort",
        "One verdict per patient, from the integrity tab - the same counts, drawn. Hover a "
        "slice or a row to highlight it. NO_RT is not a failure: those patients carry no "
        "radiotherapy object at all, so there was no chain to check.",
        f"<div class='ovpie-wrap'><div class='ovpie-stage'>{svg}</div>"
        f"<ul class='pl-list'>{''.join(legend)}</ul></div>",
        cls="ovpie-block",
    )


def overview_section_html(ov: dict) -> str:
    """The whole overview tab."""
    if ov.get("empty"):
        return ("<p class='nogap'>Nothing was indexed under this root, so there is no cohort "
                "to describe. Check the path and that the scan can read it.</p>")
    return ("<div class='overview'>"
            + _intro_block(ov) + _scanned_block(ov) + _verdict_pie_block(ov)
            + _cohort_block(ov) + _time_block(ov) + "</div>")


# --------------------------------------------------------------------------- #
# Style and behaviour
# --------------------------------------------------------------------------- #
def overview_styles() -> str:
    """CSS for the overview tab, in the report's clinical-navy language.

    One rule governs every animation here: **the resting style is the final state**. Each
    keyframe block only supplies a ``from`` frame, played with ``animation-fill-mode: both``,
    so an element is correct the moment animation is removed - by prefers-reduced-motion (the
    base sheet switches every animation off there), by an old browser, or by print. A chart
    that is only right once its animation has finished is a chart that is sometimes wrong.

    Every reveal is one-shot and short. Nothing loops, glows or keeps moving once drawn: this
    page is read by clinicians deciding what to chase, and motion that continues after the
    data has arrived only competes with it.
    """
    return """
.overview{display:flex;flex-direction:column;gap:20px}

/* ---- the card ---- */
.ovblock{position:relative;background:var(--surface);border:1px solid var(--line);
  border-radius:var(--radius);padding:20px 22px 22px;box-shadow:var(--shadow);
  animation:ovrise .45s ease-out both}
.ovblock:nth-child(1){animation-delay:.02s}
.ovblock:nth-child(2){animation-delay:.08s}
.ovblock:nth-child(3){animation-delay:.14s}
.ovblock:nth-child(4){animation-delay:.20s}
.ovblock:nth-child(5){animation-delay:.26s}
@keyframes ovrise{from{opacity:0;transform:translateY(6px)}}
/* Scroll-triggered reveal. The script arms the page (.ovarmed) only when it can also disarm
   it, then releases each card (.in) as it scrolls into view. Without the script nothing is
   ever armed and everything plays on load; either way it ends in the resting state. */
.ovarmed .ovblock:not(.in),.ovarmed .ovblock:not(.in) *,
.ovarmed .ovblock:not(.in) *::before{animation-play-state:paused}

/* The heading carries the block: larger than body text, in the header's navy, with an
   accent rule in front - a landmark, not an effect. */
.ovh{margin:0 0 8px;font-size:18px;font-weight:650;letter-spacing:-.01em;color:var(--accent-2);
  display:flex;align-items:center;gap:10px}
.ovh::before{content:"";width:4px;height:18px;border-radius:2px;background:var(--accent);flex:none}
.ovh-t{color:var(--accent-2)}
/* No max-width: a measure set in `ch` capped the lede near 500px and left every block
   ending in a half-page of empty card. The card sets the measure. */
.ovlede{margin:0 0 16px;font-size:13px;color:var(--muted);line-height:1.65}

/* ---- intro ---- */
.ovintro{background:linear-gradient(135deg,var(--accent-soft) 0%,var(--surface) 55%);
  border-top:3px solid var(--accent)}
.ovintro .ovh{font-size:21px}
.ovintro .ovlede{color:var(--text)}
.ovfeats{list-style:none;margin:0;padding:0;display:grid;gap:12px;
  grid-template-columns:repeat(auto-fit,minmax(235px,1fr))}
.ovfeat{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:13px 15px 14px;transition:border-color .15s}
.ovfeat:hover{border-color:var(--accent-line)}
.ovfeat-k{display:block;font-style:normal;font-size:11px;font-weight:700;letter-spacing:.1em;
  color:var(--accent);margin-bottom:6px}
.ovfeat b{display:block;font-size:13px;color:var(--ink);margin-bottom:4px}
.ovfeat span{display:block;font-size:12px;color:var(--muted);line-height:1.6}
.ovsource{margin:15px 0 0;font-size:11.5px;color:var(--dim);display:flex;align-items:center;
  flex-wrap:wrap;gap:6px}
.ovsrc-k{font-size:9.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--accent);background:var(--accent-soft);padding:2px 7px;border-radius:var(--radius-sm)}
.ovsource .mono{color:var(--text)}

/* ---- figures ---- */
.ovstats{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(215px,1fr))}
.ovstats-4{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.ovstat{background:var(--surface-2);border:1px solid var(--line);
  border-left:3px solid var(--accent);border-radius:var(--radius-sm);padding:12px 14px 13px}
.ovstat.warn{border-left-color:var(--warn);
  background:color-mix(in srgb,var(--warn) 6%,var(--mix-up))}
.ovnum{font-family:var(--mono);font-size:22px;font-weight:700;color:var(--ink);line-height:1.2;
  letter-spacing:-.01em;font-variant-numeric:tabular-nums}
.ovstat.warn .ovnum{color:var(--warn)}
.ovto{font-family:var(--sans);font-size:12px;font-weight:400;color:var(--dim)}
.ovlab{font-size:10.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);margin-top:4px}
.ovnote{margin:6px 0 0;font-size:12px;color:var(--dim);line-height:1.6}

/* ---- horizontal bars ---- */
.ovchart{margin:8px 0 4px}
.ovbars{display:flex;flex-direction:column;gap:8px;margin-top:18px}
.ovbar{display:grid;grid-template-columns:86px 1fr 82px;align-items:center;gap:12px}
.ovbar-lab{font-family:var(--mono);font-size:11px;font-weight:700;color:var(--ink);
  text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ovbar-track{height:12px;background:var(--surface-3);border-radius:3px;overflow:hidden}
.ovbar-fill{display:block;height:100%;width:var(--pct);border-radius:3px;background:var(--hue);
  animation:ovgrow .7s ease-out both;animation-delay:calc(.2s + var(--i)*.05s)}
@keyframes ovgrow{from{width:0}}
.ovbar-val{font-size:11.5px;color:var(--text);font-variant-numeric:tabular-nums}

/* ---- acquisition columns ---- */
.ovspan{display:flex;align-items:center;gap:12px;margin:0 0 12px;font-size:11.5px;color:var(--text)}
.ovspan-line{flex:1;height:1px;background:var(--line-strong)}
.ovcols{display:flex;align-items:flex-end;gap:3px;height:140px;padding-bottom:20px;
  border-bottom:1px solid var(--line-strong);margin-bottom:16px}
.ovcol{position:relative;flex:1;height:100%;display:flex;align-items:flex-end;min-width:4px}
.ovcol-fill{display:block;width:100%;height:var(--h);border-radius:2px 2px 0 0;
  background:color-mix(in srgb,var(--accent) 55%,var(--mix-up));
  animation:ovrisebar .6s ease-out both;animation-delay:calc(.25s + var(--i)*.012s);
  transition:background .12s}
@keyframes ovrisebar{from{height:0}}
.ovcol.tall .ovcol-fill{background:var(--accent-2)}
.ovcol:hover .ovcol-fill{background:var(--accent)}
.ovcol-lab{position:absolute;bottom:-19px;left:50%;transform:translateX(-50%);
  font-size:9.5px;font-family:var(--mono);color:var(--dim);white-space:nowrap}
.ovcol-lab.off{display:none}

/* ---- RT verdict pie ---- */
.ovpie-wrap{display:grid;grid-template-columns:minmax(230px,290px) 1fr;gap:32px;align-items:center}
.ovpie-stage{position:relative;aspect-ratio:1/1;max-width:290px;width:100%}
.ovpie{display:block;width:100%;height:100%;overflow:visible}
.pdisc{fill:var(--surface-2);stroke:var(--line);stroke-width:1}
/* A wedge rests at its final arc; the draw only supplies the empty first frame. */
.pw{stroke-dasharray:var(--len) var(--circ);stroke-dashoffset:var(--off);
  animation:pwsweep var(--d) linear var(--t0) both;
  transition:transform .2s ease-out,opacity .2s;cursor:pointer}
@keyframes pwsweep{from{stroke-dasharray:0 var(--circ)}}
.pw:hover,.ovpie .pw.hot{transform:translate(var(--dx),var(--dy))}
.ovpie.has-hot .pw:not(.hot){opacity:.35}
.psep{stroke:var(--surface);stroke-width:2;stroke-linecap:butt;pointer-events:none;
  animation:pfade .3s ease calc(var(--t0) + var(--sweep)) both}
.phub{fill:var(--surface);stroke:var(--line);stroke-width:1}
.phub,.ptotal,.psub{animation:pfade .35s ease calc(var(--t0) + var(--sweep) - .15s) both}
.ptotal{text-anchor:middle;font-family:var(--mono);font-size:19px;font-weight:700;fill:var(--ink)}
.psub{text-anchor:middle;font-size:7px;font-weight:700;letter-spacing:.14em;
  text-transform:uppercase;fill:var(--muted)}
@keyframes pfade{from{opacity:0}}

/* The legend: one row per verdict, its share drawn as a bar beneath. */
.pl-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.pl{display:grid;grid-template-columns:11px auto 1fr auto auto;
  grid-template-areas:"dot name note n pct" "bar bar bar bar bar";
  align-items:center;gap:6px 11px;padding:10px 13px 11px;border-radius:var(--radius-sm);
  border:1px solid var(--line);border-left:3px solid var(--c);background:var(--surface);
  cursor:default;transition:background .15s,border-color .15s}
.pl:hover,.pl:focus-visible,.pl.hot{outline:none;background:var(--surface-2);
  border-color:color-mix(in srgb,var(--c) 45%,transparent);border-left-color:var(--c)}
.pl-dot{grid-area:dot;width:10px;height:10px;border-radius:2px;background:var(--c)}
.pl-name{grid-area:name;font-family:var(--mono);font-size:11px;font-weight:700;
  letter-spacing:.05em;color:var(--ink)}
.pl-note{grid-area:note;font-size:12px;color:var(--dim);min-width:0}
.pl-n{grid-area:n;font-size:17px;font-weight:700;color:var(--ink);text-align:right}
.pl-pct{grid-area:pct;font-size:11.5px;font-weight:700;color:var(--c);min-width:40px;text-align:right}
.pl-bar{grid-area:bar;height:4px;border-radius:2px;background:var(--surface-3);overflow:hidden}
.pl-bar i{display:block;height:100%;width:var(--pct);border-radius:2px;background:var(--c);
  animation:ovgrow .7s ease-out both;animation-delay:calc(.35s + var(--i)*.06s)}

/* ---- exact numbers, one click away ---- */
.ovdetails{margin-top:16px}
.ovdetails>summary{cursor:pointer;font-size:12px;font-weight:600;color:var(--accent);
  padding:4px 0;width:max-content}
.ovdetails>summary:hover{text-decoration:underline}
.ovtable{margin-top:10px}
.ovtable .ovmod{font-family:var(--mono);font-weight:700;color:var(--ink)}
.ovtable th.ovn,.ovtable td.ovn{text-align:right;white-space:nowrap}
.ovempty{font-size:12px;color:var(--dim);margin:4px 0}
.ovlegend{list-style:none;margin:0;padding:0;font-size:11.5px;color:var(--muted);line-height:2}
.ovlegend b{color:var(--ink);font-family:var(--mono)}
.ovdot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:7px}

@media (max-width:760px){
  .ovstats,.ovstats-4{grid-template-columns:1fr}
  .ovpie-wrap{grid-template-columns:1fr;justify-items:center}
  .pl-list{width:100%}
  .pl{grid-template-areas:"dot name n pct" "note note note note" "bar bar bar bar";
    grid-template-columns:11px 1fr auto auto}
  .ovbar{grid-template-columns:70px 1fr 62px}
}
/* The resting style already is the final state; this block says so for every chart. */
@media (prefers-reduced-motion:reduce){
  .ovblock{animation:none;opacity:1;transform:none}
  .ovbar-fill,.pl-bar i{animation:none;width:var(--pct)}
  .ovcol-fill{animation:none;height:var(--h)}
  .pw{animation:none;stroke-dasharray:var(--len) var(--circ)}
}
/* Paper has no timeline: print every card in its resting, final state. */
@media print{.overview *,.overview *::before{animation:none!important}}
"""


def overview_script() -> str:
    """Behaviour for the overview: dial and legend linked on hover, headline figures counted up.

    The markup already carries every final number, so the count-up only ever replaces a
    correct value with the same correct value. If the script never runs - JS off, an error
    earlier on the page, a printed copy - the page still reads correctly, which is why the
    animation is not allowed to be the thing that writes the number.

    Linking runs regardless of motion preferences (it is information, not animation); the
    count-up is skipped entirely under reduced motion.
    """
    return r"""
(function(){
  "use strict";
  // ---- dial <-> legend: hovering or focusing either singles the verdict out in both ----
  Array.prototype.slice.call(document.querySelectorAll('.ovpie-wrap')).forEach(function(wrap){
    var svg = wrap.querySelector('.ovpie');
    var parts = Array.prototype.slice.call(wrap.querySelectorAll('[data-v]'));
    function light(v){
      parts.forEach(function(el){
        if(el.classList) el.classList.toggle('hot', el.getAttribute('data-v') === v);
      });
      if(svg && svg.classList) svg.classList.toggle('has-hot', !!v);
    }
    parts.forEach(function(el){
      var v = el.getAttribute('data-v');
      el.addEventListener('mouseenter', function(){ light(v); });
      el.addEventListener('mouseleave', function(){ light(null); });
      el.addEventListener('focus', function(){ light(v); });
      el.addEventListener('blur', function(){ light(null); });
    });
  });

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(reduce) return;

  // ---- release each card's choreography as it scrolls into view ----
  // Armed only when IntersectionObserver exists: arming without a way to release would leave
  // every card paused on its empty first frame.
  if('IntersectionObserver' in window){
    var cards = Array.prototype.slice.call(document.querySelectorAll('.ovblock'));
    if(cards.length){
      document.documentElement.classList.add('ovarmed');
      function release(c){ c.classList.add('in'); }
      var cardIo = new IntersectionObserver(function(entries){
        entries.forEach(function(e){
          if(e.isIntersecting && e.target.getClientRects().length){
            release(e.target); cardIo.unobserve(e.target);
          }
        });
      }, {threshold:.18});
      cards.forEach(function(c){ cardIo.observe(c); });
      // The observer alone is not enough: in testing it never fired in a backgrounded
      // browser pane, which left every card paused on its invisible first frame - a blank
      // page. A plain geometry check on scroll backs it up, and a hard timer releases every
      // card no matter what, so no path can end on an empty report.
      function sweep(){
        var h = window.innerHeight || document.documentElement.clientHeight;
        cards.forEach(function(c){
          if(c.classList.contains('in')) return;
          var r = c.getBoundingClientRect();
          if(r.height && r.top < h * .85 && r.bottom > 0) release(c);
        });
      }
      window.addEventListener('scroll', sweep, {passive:true});
      window.addEventListener('resize', sweep);
      document.addEventListener('click', function(){ setTimeout(sweep, 60); });
      setTimeout(sweep, 120);
      setTimeout(function(){ cards.forEach(release); }, 6000);
    }
  }

  // ---- count-up on the headline figures, once, when the tab is actually shown ----
  var nums = Array.prototype.slice.call(document.querySelectorAll('[data-count]'));
  if(!nums.length) return;
  var THIN = ' ';
  function fmt(n){ return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, THIN); }
  // getClientRects is empty for anything inside a display:none tab panel, for HTML and SVG
  // alike - offsetParent does not exist on SVG text, so it cannot be the test.
  function shown(el){ return el.getClientRects().length > 0; }
  function run(el){
    if(el.getAttribute('data-counted')) return;
    el.setAttribute('data-counted','1');
    var target = parseInt(el.getAttribute('data-count'), 10);
    if(!isFinite(target) || target <= 0) return;
    var final = el.textContent, t0 = null, DUR = 800;
    // The dial's total waits for its hub to arrive: a number counting inside an invisible
    // lens is a number nobody saw count.
    var wait = el.classList && el.classList.contains('ptotal') ? 1250 : 0;
    function step(ts){
      if(t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / DUR);
      var v = Math.round(target * (1 - Math.pow(1 - p, 3)));   // ease-out cubic
      el.textContent = p < 1 ? fmt(v) : final;
      if(p < 1) requestAnimationFrame(step);
    }
    setTimeout(function(){ requestAnimationFrame(step); }, wait);
    // A floor under the animation: requestAnimationFrame is suspended in a background tab,
    // which froze one figure mid-count in testing. Whatever happens to the frames, the true
    // number is back in place shortly after the count should have ended.
    setTimeout(function(){ el.textContent = final; }, wait + DUR + 400);
  }
  if(!('IntersectionObserver' in window)){ nums.forEach(run); return; }
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      if(e.isIntersecting && shown(e.target)){ run(e.target); io.unobserve(e.target); }
    });
  }, {threshold:.4});
  nums.forEach(function(el){ io.observe(el); });
})();
"""
