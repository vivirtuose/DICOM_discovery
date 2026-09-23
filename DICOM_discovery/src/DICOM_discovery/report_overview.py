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

#: One hue per modality, matching the cohort map's marker colours so a reader who learns the
#: palette on one view keeps it on the other.
_MOD_COLOR = {
    "CT": "#4a6fa5", "MR": "#5b8c7b", "PT": "#8a6fa8",
    "RTSTRUCT": "#c08a3e", "RTPLAN": "#a05a5a", "RTDOSE": "#7a7f8a", "REG": "#6b8ea3",
}
_MOD_FALLBACK = "#8b93a0"

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


def _protocol_split(comp_long: Optional[pd.DataFrame]) -> Optional[dict]:
    """Patients split into complete / incomplete / ungradeable, for the ring chart.

    Derived from ``comp_long`` directly rather than from a second engine, so the ring and the
    integrity tab cannot disagree about who is complete. The three buckets are mutually
    exclusive by construction and sum to the number of patients the protocol grades.
    """
    if comp_long is None or getattr(comp_long, "empty", True):
        return None
    n_complete = n_incomplete = n_ungradeable = n_vacuous = 0
    for _patient, pdf in comp_long.groupby("patient", sort=False):
        states = set(pdf["state"].astype(str))
        if states == {"UNMAPPED"}:
            n_ungradeable += 1
        elif "MISSING" in states:
            n_incomplete += 1
        elif bool(pdf["expected"].any()):
            n_complete += 1
        else:
            # The protocol asks nothing of this patient: not complete, not incomplete. Folding
            # them into "complete" is the inflation completeness_kpis refuses to commit.
            n_vacuous += 1
    return {"n_complete": n_complete, "n_incomplete": n_incomplete,
            "n_ungradeable": n_ungradeable, "n_nothing_expected": n_vacuous,
            "n_graded": n_complete + n_incomplete}


def cohort_overview(table: Optional[pd.DataFrame], manifest: Optional[dict] = None,
                    comp_long: Optional[pd.DataFrame] = None) -> dict:
    """Describe the cohort as indexed. Pure data in, plain data out.

    ``comp_long`` is optional and supplies only the protocol split — the numbers this page
    borrows from the completeness engine, so the front page and the integrity tab cannot
    disagree about who is complete or who is ungradeable.
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
        "protocol_split": _protocol_split(comp_long),
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
    klass = f"ovblock {cls}".strip()
    return (f"<section class='{klass}'><h3 class='ovh'>{_esc(title)}</h3>"
            f"<p class='ovlede'>{_esc(lede)}</p>{body}</section>")


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


#: Ring geometry. r=54 in a 140-box leaves room for the stroke without a viewBox clip.
_RING_R = 54.0
_RING_C = 2 * 3.141592653589793 * _RING_R


def _ring_chart(split: dict) -> str:
    """Complete / incomplete / ungradeable as one ring, drawn by animating stroke-dashoffset.

    Three arcs on one circle rather than three numbers: the question this answers ("how much
    of the cohort is actually usable?") is a proportion, and a proportion is the one thing a
    row of counts does not show.
    """
    segs = [
        ("complete", split["n_complete"], "var(--ok)"),
        ("incomplete", split["n_incomplete"], "var(--incomplete)"),
        ("ungradeable", split["n_ungradeable"], "var(--warn)"),
    ]
    total = sum(n for _, n, _ in segs) or 1
    arcs, offset = [], 0.0
    for i, (name, n, color) in enumerate(segs):
        if n <= 0:
            continue
        length = _RING_C * n / total
        arcs.append(
            f"<circle class='ovring-seg' cx='70' cy='70' r='{_RING_R}' fill='none' "
            f"stroke='{color}' stroke-width='15' stroke-linecap='butt' "
            f"style='--len:{length:.2f};--gap:{_RING_C:.2f};--off:{-offset:.2f};--i:{i}'>"
            f"<title>{_esc(name)}: {n} of {total} patients</title></circle>"
        )
        offset += length
    pct = round(100.0 * split["n_complete"] / total)
    legend = "".join(
        f"<li><span class='ovdot' style='background:{c}'></span>"
        f"<b>{_num(n)}</b> {_esc(name)}</li>"
        for name, n, c in segs if n > 0
    )
    return (
        "<div class='ovring-wrap'>"
        "<svg class='ovring' viewBox='0 0 140 140' role='img' "
        f"aria-label='{pct}% of graded patients are complete'>"
        f"<circle cx='70' cy='70' r='{_RING_R}' fill='none' stroke='var(--line)' "
        "stroke-width='15'></circle>"
        f"{''.join(arcs)}"
        f"<text class='ovring-num' x='70' y='70'>{pct}%</text>"
        "<text class='ovring-sub' x='70' y='88'>complete</text>"
        "</svg>"
        f"<ul class='ovlegend'>{legend}</ul></div>"
    )


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
    ("Answers in one self-contained file",
     "This page carries its own styles and scripts. It opens by double-click on an "
     "air-gapped machine, with CSV and JSON exports beside it for anything downstream."),
]


def _intro_block(ov: dict) -> str:
    cards = "".join(
        f"<li class='ovfeat' style='--i:{i}'><b>{_esc(t)}</b><span>{_esc(d)}</span></li>"
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


def _protocol_block(ov: dict) -> str:
    split = ov.get("protocol_split")
    if not split or split["n_graded"] + split["n_ungradeable"] == 0:
        return ""
    extra = []
    if split["n_ungradeable"]:
        extra.append(_stat(
            _num(split["n_ungradeable"]), "cannot be placed on the timeline", cls="warn",
            count=split["n_ungradeable"],
            note="Not one of their studies fell inside a protocol window - ungradeable, "
                 "which is not the same claim as incomplete."))
    if split["n_nothing_expected"]:
        extra.append(_stat(
            _num(split["n_nothing_expected"]), "nothing expected", count=split["n_nothing_expected"],
            note="The protocol asks nothing of them. Not counted as complete: there was "
                 "nothing to be complete about."))
    return _block(
        "Follow-up against the protocol",
        "How much of the cohort actually holds what the protocol asks for. The integrity tab "
        "says which timepoint and modality are missing, and for whom.",
        f"<div class='ovsplit'>{_ring_chart(split)}"
        f"<div class='ovstats ovstats-stack'>{''.join(extra)}</div></div>",
    )


def overview_section_html(ov: dict) -> str:
    """The whole overview tab."""
    if ov.get("empty"):
        return ("<p class='nogap'>Nothing was indexed under this root, so there is no cohort "
                "to describe. Check the path and that the scan can read it.</p>")
    return ("<div class='overview'>"
            + _intro_block(ov) + _scanned_block(ov) + _cohort_block(ov)
            + _time_block(ov) + _protocol_block(ov) + "</div>")


# --------------------------------------------------------------------------- #
# Style and behaviour
# --------------------------------------------------------------------------- #
def overview_styles() -> str:
    """CSS for the overview tab, in the cohort report's own design language.

    Every animation is a one-shot reveal, never a loop: a page a clinician reads for two
    minutes must settle. All of them are switched off wholesale under
    ``prefers-reduced-motion``, where each element is given its final geometry directly — the
    chart must be correct in its resting state, not merely correct once the animation ends.
    """
    return """
.overview{display:flex;flex-direction:column;gap:20px}
.ovblock{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:17px 19px 19px;opacity:0;transform:translateY(8px);
  animation:ovrise .5s cubic-bezier(.22,1,.36,1) forwards}
.ovblock:nth-child(1){animation-delay:.02s}
.ovblock:nth-child(2){animation-delay:.10s}
.ovblock:nth-child(3){animation-delay:.18s}
.ovblock:nth-child(4){animation-delay:.26s}
.ovblock:nth-child(5){animation-delay:.34s}
@keyframes ovrise{to{opacity:1;transform:none}}
.ovh{margin:0 0 4px;font-size:13.5px;font-weight:600;color:var(--ink);letter-spacing:.01em}
.ovlede{margin:0 0 14px;font-size:12px;color:var(--muted);line-height:1.65;max-width:82ch}

/* ---- the intro card: what the tool is, before what the cohort is ---- */
.ovintro{border-left:3px solid var(--accent-line)}
.ovfeats{list-style:none;margin:0;padding:0;display:grid;gap:10px;
  grid-template-columns:repeat(auto-fit,minmax(245px,1fr))}
.ovfeat{background:var(--surface-2);border:1px solid var(--line);border-radius:var(--radius-sm);
  padding:11px 13px;opacity:0;animation:ovrise .45s cubic-bezier(.22,1,.36,1) forwards;
  animation-delay:calc(.18s + var(--i)*.07s);transition:border-color .18s,transform .18s}
.ovfeat:hover{border-color:var(--accent-line);transform:translateY(-1px)}
.ovfeat b{display:block;font-size:12px;color:var(--ink);margin-bottom:3px}
.ovfeat span{font-size:11.5px;color:var(--dim);line-height:1.55}
.ovsource{margin:13px 0 0;font-size:11.5px;color:var(--dim)}
.ovsrc-k{display:inline-block;font-size:10px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);margin-right:8px}

/* ---- figures ---- */
.ovstats{display:grid;gap:11px;grid-template-columns:repeat(auto-fit,minmax(215px,1fr))}
.ovstats-4{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.ovstats-stack{grid-template-columns:1fr;align-content:start}
.ovstat{background:var(--surface-2);border:1px solid var(--line);
  border-left:3px solid var(--accent-line);border-radius:var(--radius-sm);padding:10px 13px 11px}
.ovstat.warn{border-left-color:var(--warn)}
.ovnum{font-family:var(--mono);font-size:19px;font-weight:700;color:var(--ink);line-height:1.25;
  font-variant-numeric:tabular-nums}
.ovstat.warn .ovnum{color:var(--warn)}
.ovto{font-family:var(--sans);font-size:12px;font-weight:400;color:var(--dim)}
.ovlab{font-size:11.5px;font-weight:600;color:var(--muted);margin-top:2px}
.ovnote{margin:6px 0 0;font-size:11.5px;color:var(--dim);line-height:1.55}

/* ---- horizontal bars ---- */
.ovchart{margin:6px 0 4px}
.ovbars{display:flex;flex-direction:column;gap:7px}
.ovbar{display:grid;grid-template-columns:86px 1fr 78px;align-items:center;gap:10px}
.ovbar-lab{font-family:var(--mono);font-size:11px;font-weight:600;color:var(--ink);
  text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ovbar-track{height:16px;background:var(--surface-2);border-radius:3px;overflow:hidden}
.ovbar-fill{display:block;height:100%;width:0;border-radius:3px;
  background:linear-gradient(90deg,color-mix(in srgb,var(--hue) 82%,#fff) 0%,var(--hue) 100%);
  animation:ovgrow .85s cubic-bezier(.22,1,.36,1) forwards;
  animation-delay:calc(.24s + var(--i)*.06s)}
@keyframes ovgrow{to{width:var(--pct)}}
.ovbar-val{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}

/* ---- acquisition columns ---- */
.ovspan{display:flex;align-items:center;gap:10px;margin:0 0 10px;font-size:11px;
  color:var(--muted)}
.ovspan-line{flex:1;height:1px;background:linear-gradient(90deg,var(--line-strong),var(--line))}
.ovcols{display:flex;align-items:flex-end;gap:3px;height:132px;padding-bottom:20px;
  border-bottom:1px solid var(--line)}
.ovcol{position:relative;flex:1;height:100%;display:flex;align-items:flex-end;min-width:4px}
.ovcol-fill{display:block;width:100%;height:0;border-radius:2px 2px 0 0;
  background:color-mix(in srgb,var(--accent) 55%,transparent);
  animation:ovrisebar .7s cubic-bezier(.22,1,.36,1) forwards;
  animation-delay:calc(.3s + var(--i)*.018s);transition:background .15s}
.ovcol.tall .ovcol-fill{background:var(--accent)}
.ovcol:hover .ovcol-fill{background:var(--ink)}
@keyframes ovrisebar{to{height:var(--h)}}
.ovcol-lab{position:absolute;bottom:-19px;left:50%;transform:translateX(-50%);
  font-size:9.5px;font-family:var(--mono);color:var(--dim);white-space:nowrap}
.ovcol-lab.off{display:none}

/* ---- protocol ring ---- */
.ovsplit{display:grid;grid-template-columns:minmax(190px,240px) 1fr;gap:18px;align-items:center}
.ovring-wrap{display:flex;align-items:center;gap:16px}
.ovring{width:140px;height:140px;flex:none;transform:rotate(-90deg)}
.ovring-seg{stroke-dasharray:var(--len) var(--gap);stroke-dashoffset:var(--off);
  opacity:0;animation:ovarc .8s cubic-bezier(.22,1,.36,1) forwards;
  animation-delay:calc(.35s + var(--i)*.14s)}
@keyframes ovarc{from{stroke-dasharray:0 var(--gap)}to{opacity:1;stroke-dasharray:var(--len) var(--gap)}}
.ovring-num{transform:rotate(90deg);transform-origin:70px 70px;text-anchor:middle;
  font-family:var(--mono);font-size:25px;font-weight:700;fill:var(--ink)}
.ovring-sub{transform:rotate(90deg);transform-origin:70px 70px;text-anchor:middle;
  font-size:10px;fill:var(--muted);letter-spacing:.04em}
.ovlegend{list-style:none;margin:0;padding:0;font-size:11.5px;color:var(--muted);line-height:2}
.ovlegend b{color:var(--ink);font-family:var(--mono)}
.ovdot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:7px}

/* ---- exact numbers, one click away ---- */
.ovdetails{margin-top:14px}
.ovdetails>summary{cursor:pointer;font-size:11.5px;font-weight:600;color:var(--accent);
  padding:4px 0}
.ovtable{margin-top:10px}
.ovtable .ovmod{font-family:var(--mono);font-weight:600;color:var(--ink)}
.ovtable th.ovn,.ovtable td.ovn{text-align:right;white-space:nowrap}
.ovempty{font-size:12px;color:var(--dim);margin:4px 0}

@media (max-width:760px){
  .ovstats,.ovstats-4{grid-template-columns:1fr}
  .ovsplit{grid-template-columns:1fr}
  .ovbar{grid-template-columns:70px 1fr 62px}
}
/* The resting state must be the correct state: no motion, final geometry, same numbers. */
@media (prefers-reduced-motion:reduce){
  .ovblock,.ovfeat{opacity:1;transform:none;animation:none}
  .ovbar-fill{animation:none;width:var(--pct)}
  .ovcol-fill{animation:none;height:var(--h)}
  .ovring-seg{animation:none;opacity:1}
  .ovfeat:hover{transform:none}
}
"""


def overview_script() -> str:
    """Count-up on the headline figures, run once when the tab is first shown.

    The markup already carries the final number, so this only ever replaces a correct value
    with the same correct value. If the script never runs — JS off, an error earlier on the
    page, a printed copy — the page still reads correctly, which is why the animation is not
    allowed to be the thing that writes the number.
    """
    return r"""
(function(){
  "use strict";
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(reduce) return;
  var nums = Array.prototype.slice.call(document.querySelectorAll('.ovnum[data-count]'));
  if(!nums.length) return;
  var THIN = ' ';
  function fmt(n){ return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, THIN); }
  function run(el){
    if(el.getAttribute('data-counted')) return;
    el.setAttribute('data-counted','1');
    var target = parseInt(el.getAttribute('data-count'), 10);
    if(!isFinite(target) || target <= 0) return;
    var final = el.textContent, t0 = null, DUR = 700;
    function step(ts){
      if(t0 === null) t0 = ts;
      var p = Math.min(1, (ts - t0) / DUR);
      // ease-out cubic: fast first, settling — the same curve the bars grow on.
      var v = Math.round(target * (1 - Math.pow(1 - p, 3)));
      el.textContent = p < 1 ? fmt(v) : final;
      if(p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  if(!('IntersectionObserver' in window)){ nums.forEach(run); return; }
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(e){
      // offsetParent is null inside a hidden tab panel: wait until the tab is actually shown
      // rather than burning the one-shot animation on a panel nobody is looking at.
      if(e.isIntersecting && e.target.offsetParent !== null){ run(e.target); io.unobserve(e.target); }
    });
  }, {threshold:.4});
  nums.forEach(function(el){ io.observe(el); });
})();
"""
