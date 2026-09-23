"""Cohort overview — the context a reader needs *before* any per-patient verdict.

The report used to open on a work queue: 98 rows of patients, each with a verdict. That page
answers "which patient is broken?" for someone who already knows what the cohort is. It
answers nothing at all for someone opening the file for the first time, who does not yet know
how many patients there are, over what period, from which folder, or how much of the tree
could not be read — and therefore has no way to judge whether the verdicts below are about
the data they think they are about.

This module builds that missing first page. It is deliberately split in two halves:

* :func:`cohort_overview` computes numbers from the canonical index and the run manifest and
  returns plain data — no HTML, no pandas in the result — so every figure on the page can be
  asserted in a test;
* :func:`overview_section_html` renders them, and every figure it renders carries one line
  saying what it means and why it is worth looking at.

Two rules govern what goes on this page.

**Every number is labelled as the thing it actually counts.** The index collapses an image
series to one row, so "rows" and "files" are different facts by roughly the mean slice count.
They appear under two names, never one.

**Nothing here is a verdict.** The RT-integrity and completeness tabs grade the cohort against
a standard and a protocol. This tab only describes it. A reader must be able to check that the
description matches what they expected *before* being told what is wrong with it.
"""
from __future__ import annotations

import datetime
from typing import List, Optional

import pandas as pd

from .tg263 import is_tg263_conformant

#: Modalities in the order a radiotherapy chain is acquired, so the table reads as a workflow
#: rather than as an alphabet. Anything unlisted sorts after, alphabetically.
_MOD_ORDER = {"CT": 0, "MR": 1, "PT": 2, "RTSTRUCT": 3, "RTPLAN": 4, "RTDOSE": 5, "REG": 6}


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
                "followup_days": None, "n_patients_dated": 0}

    spans = [(row["last"] - row["first"]).days for _, row in per_patient.iterrows()
             if row["n_studies"] > 1]
    counts = per_patient["n_studies"]
    return {
        "studies_per_patient": {"min": int(counts.min()),
                                "median": float(counts.median()),
                                "max": int(counts.max())},
        # A patient with one study cannot be incomplete on follow-up — there is no follow-up
        # to be missing. Counting them as "incomplete" elsewhere without saying how many they
        # are is how a recent inclusion gets chased for data that does not exist yet.
        "n_single_study_patients": int((counts == 1).sum()),
        "followup_days": ({"median": float(pd.Series(spans).median()), "max": int(max(spans))}
                          if spans else None),
        "n_patients_dated": int(len(per_patient)),
    }


def _trust(table: pd.DataFrame, manifest: dict) -> dict:
    """The figures that decide whether the rest of the report is about the right data.

    Grouping by patient is the load-bearing assumption of every other tab. If the identifier
    came from a folder name rather than from the DICOM tag, that grouping rests on a naming
    convention nobody validated — worth one line on the front page, not a footnote.
    """
    ids = table[["patient_id", "patient_id_source"]].astype(str).drop_duplicates()
    id_sources = [{"source": src, "n_patients": int(n)}
                  for src, n in ids["patient_id_source"].value_counts().items()]

    # More than one frame of reference in a patient means their CT, structures and dose are
    # not all in one geometry: the RT chain may be internally consistent and still not
    # registered. This counts the patients worth looking at, not the frames.
    fors = table[["patient_id", "frame_of_reference"]].astype(str)
    fors = fors[fors["frame_of_reference"].str.strip() != ""]
    n_multi_for = int((fors.groupby("patient_id")["frame_of_reference"].nunique() > 1).sum())

    roi_names: List[str] = []
    for cell in table.loc[table["modality"].astype(str) == "RTSTRUCT", "roi_names"]:
        if isinstance(cell, (list, tuple)):
            roi_names.extend(str(n) for n in cell)
    n_rois = len(roi_names)
    n_conformant = sum(1 for n in roi_names if is_tg263_conformant(n))

    return {
        "id_sources": id_sources,
        "n_multi_frame_of_reference_patients": n_multi_for,
        "n_rois": n_rois,
        "n_rois_tg263": n_conformant,
        "pct_rois_tg263": round(100.0 * n_conformant / n_rois, 1) if n_rois else None,
        "n_files_seen": int(manifest.get("n_files_seen", 0) or 0),
        "n_unreadable": int(manifest.get("n_unreadable", 0) or 0),
        "n_dirs_unreadable": int(manifest.get("n_dirs_unreadable", 0) or 0),
        "n_dirs_excluded": int(manifest.get("n_dirs_excluded", 0) or 0),
    }


def cohort_overview(table: Optional[pd.DataFrame], manifest: Optional[dict] = None,
                    comp_long: Optional[pd.DataFrame] = None) -> dict:
    """Describe the cohort as indexed. Pure data in, plain data out.

    ``comp_long`` is optional and only supplies the count of patients no protocol window could
    place — the one number this page borrows from the completeness engine, so the front page
    and the completeness tab cannot disagree about how many patients are ungradeable.
    """
    manifest = manifest or {}
    if table is None or getattr(table, "empty", True):
        return {"empty": True, "root": str(manifest.get("root", "") or ""),
                "generated_utc": str(manifest.get("generated_utc", "") or "")}

    dates = table["study_date"].map(_parse_date)
    dated_studies = table.loc[dates.notna(), "study_uid"]
    undated_studies = table.loc[dates.isna(), "study_uid"]

    n_unplaceable = 0
    if comp_long is not None and not getattr(comp_long, "empty", True):
        by_patient = comp_long.groupby("patient")["state"].apply(lambda s: set(s))
        n_unplaceable = int(sum(1 for states in by_patient if states == {"UNMAPPED"}))

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
        # Studies with no readable date: the single most common reason a complete patient is
        # graded ungradeable, so it belongs on the front page and not in a log line.
        "n_undated_studies": _nunique_nonempty(undated_studies) if len(undated_studies) else 0,
        "n_dated_studies": _nunique_nonempty(dated_studies) if len(dated_studies) else 0,
        "n_unplaceable_patients": n_unplaceable,
    }
    ov.update(_per_patient_time(table))
    ov.update(_trust(table, manifest))
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


def _stat(value: str, label: str, note: str = "", cls: str = "") -> str:
    """One figure, its name, and one sentence on what it tells you.

    The sentence is not decoration. The complaint this page answers was that a first-time
    reader understands nothing; a bare number with a terse label is exactly what produced
    that, so no figure ships here without its sentence.
    """
    cls = f"ovstat {cls}".strip()
    return (f"<div class='{cls}'><div class='ovnum'>{value}</div>"
            f"<div class='ovlab'>{_esc(label)}</div>"
            f"<p class='ovnote'>{_esc(note)}</p></div>")


def _block(title: str, lede: str, body: str) -> str:
    return (f"<section class='ovblock'><h3 class='ovh'>{_esc(title)}</h3>"
            f"<p class='ovlede'>{_esc(lede)}</p>{body}</section>")


def _scanned_block(ov: dict) -> str:
    stats = [
        _stat(_num(ov["n_files_seen"]), "files walked",
              "Every file the scan opened a directory entry for, DICOM or not."),
        _stat(_num(ov["n_files"]), "DICOM files indexed",
              "Of those, the ones that are DICOM and were read. Headers only - no pixel data "
              "is ever loaded."),
        _stat(_num(ov["n_rows"]), "series and RT objects",
              "The rows this report works on: an image series counts once however many slices "
              "it holds, and each RT object counts once."),
    ]
    missed = ov["n_unreadable"] + ov["n_dirs_unreadable"]
    stats.append(_stat(
        _num(missed), "not read", cls="warn" if missed else "",
        note="Files that could not be read plus directories that could not be listed. Anything "
        "here is data this report does not know about - a clean cohort with unreadable "
        "corners is not a clean cohort."))
    return _block(
        "What was scanned",
        "Before any verdict: which tree was read, and how much of it came back.",
        f"<div class='ovstats'>{''.join(stats)}</div>",
    )


def _cohort_block(ov: dict) -> str:
    stats = [
        _stat(_num(ov["n_patients"]), "patients",
              "Distinct patient identifiers after grouping - see “Can you trust this page” "
              "below for where those identifiers came from."),
        _stat(_num(ov["n_studies"]), "studies",
              "Distinct StudyInstanceUIDs. One study is one visit to the scanner."),
        _stat(_num(ov["n_series"]), "series",
              "Distinct SeriesInstanceUIDs - one acquisition within a study."),
    ]
    rows = "".join(
        f"<tr><td class='ovmod'>{_esc(m['modality'])}</td>"
        f"<td class='ovn mono'>{_num(m['n_files'])}</td>"
        f"<td class='ovn mono'>{_num(m['n_series'])}</td>"
        f"<td class='ovn mono'>{_num(m['n_studies'])}</td>"
        f"<td class='ovn mono'>{_num(m['n_patients'])}</td></tr>"
        for m in ov["modalities"]
    )
    table = (f"<table class='grid ovtable'><thead><tr><th>Modality</th>"
             f"<th class='ovn'>Files</th><th class='ovn'>Series</th>"
             f"<th class='ovn'>Studies</th><th class='ovn'>Patients</th></tr></thead>"
             f"<tbody>{rows}</tbody></table>")
    return _block(
        "What the cohort holds",
        "The same objects counted four ways, because the four answer different questions: "
        "8 000 CT files across 40 series in 40 patients is an ordinary cohort, while 8 000 "
        "files in one series is a grouping mistake.",
        f"<div class='ovstats'>{''.join(stats)}</div>{table}",
    )


def _time_block(ov: dict) -> str:
    span = ov.get("date_min") and ov.get("date_max")
    stats = [_stat(
        f"{_esc(ov['date_min'])} <span class='ovto'>to</span> {_esc(ov['date_max'])}" if span
        else "&mdash;", "study dates", cls="wide",
        note="Earliest and latest DICOM StudyDate in the cohort. If this window is not the one you "
        "expected, you are looking at the wrong folder.")]

    spp = ov.get("studies_per_patient")
    if spp:
        stats.append(_stat(
            f"{spp['min']} <span class='ovto'>/</span> {spp['median']:g} "
            f"<span class='ovto'>/</span> {spp['max']}",
            "studies per patient (min / median / max)",
            "A wide spread usually means the cohort mixes patients at different stages of "
            "follow-up, not that data is missing."))

    fu = ov.get("followup_days")
    if fu:
        stats.append(_stat(
            f"{_num(fu['median'])} <span class='ovto'>d</span>", "median follow-up",
            "Days between a patient's first and last dated study, over patients with more "
            f"than one. Longest in this cohort: {_num(fu['max'])} days."))

    n_single = ov.get("n_single_study_patients", 0)
    if n_single:
        stats.append(_stat(
            _num(n_single), "patients with a single study",
            "These have no follow-up to be missing. Chasing them for later timepoints asks "
            "for data that does not exist yet."))

    n_undated = ov.get("n_undated_studies", 0)
    if n_undated:
        stats.append(_stat(
            _num(n_undated), "studies with no readable date", cls="warn",
            note="No StudyDate, or one that will not parse. These cannot be placed on any protocol "
            "timeline, which is why a complete patient can still come out ungradeable."))

    n_unplaceable = ov.get("n_unplaceable_patients", 0)
    if n_unplaceable:
        stats.append(_stat(
            _num(n_unplaceable), "patients that cannot be placed", cls="warn",
            note="Not one of their studies fell inside a protocol window. The Completeness tab "
            "marks them ungradeable rather than incomplete - the difference matters."))

    return _block(
        "The cohort in time",
        "A longitudinal protocol is a claim about dates. These are the dates it has to work "
        "with.",
        f"<div class='ovstats'>{''.join(stats)}</div>",
    )


def _trust_block(ov: dict) -> str:
    srcs = ov.get("id_sources") or []
    from_tag = sum(s["n_patients"] for s in srcs if s["source"] in ("tag", "PatientID"))
    from_folder = sum(s["n_patients"] for s in srcs if s["source"] == "folder")
    other = sum(s["n_patients"] for s in srcs) - from_tag - from_folder

    parts = [f"{_num(from_tag)} from the DICOM PatientID tag"] if from_tag else []
    if from_folder:
        parts.append(f"{_num(from_folder)} from the folder name")
    if other:
        parts.append(f"{_num(other)} from a supplied pattern")
    where = ", ".join(parts) or "unknown"

    stats = [_stat(
        _esc(where), "where the patient identifier came from",
        cls="wide" + (" warn" if from_folder else ""),
        note="Every per-patient number in this report depends on this grouping. An identifier "
             "read from a folder name rests on a naming convention, not on the DICOM header.")]

    n_multi = ov.get("n_multi_frame_of_reference_patients", 0)
    if n_multi:
        stats.append(_stat(
            _num(n_multi), "patients with several frames of reference", cls="warn",
            note="Their images, structures and dose are not all in one geometry. The RT chain can "
            "be internally consistent and still not be registered."))

    pct = ov.get("pct_rois_tg263")
    if pct is not None:
        stats.append(_stat(
            f"{pct:g}<span class='ovto'>%</span>", "ROI names following TG-263",
            f"{_num(ov['n_rois_tg263'])} of {_num(ov['n_rois'])} structure names match the "
            "TG-263 convention. A low share is a naming problem, not a contouring problem - "
            "but it breaks any script that selects structures by name."))

    n_excl = ov.get("n_dirs_excluded", 0)
    if n_excl:
        stats.append(_stat(
            _num(n_excl), "directories excluded",
            "Skipped on purpose - NAS system folders (snapshots, recycle bins) and anything "
            "passed to --exclude-dir."))

    return _block(
        "Can you trust this page",
        "The figures that decide whether everything above is about the data you think it is.",
        f"<div class='ovstats'>{''.join(stats)}</div>",
    )


def _next_block() -> str:
    items = [
        ("RT integrity", "Does each patient's radiotherapy chain hold together - CT to "
                         "structures to plan to dose, in one geometry? One verdict per patient, "
                         "worst first."),
        ("Cohort map", "Every study placed on its real acquisition date, one row per patient. "
                       "This is where a gap in time is visible as a gap."),
        ("Completeness", "The same cohort graded against a longitudinal protocol: which "
                         "timepoint and modality the most patients are missing, and who they are."),
    ]
    lis = "".join(f"<li><b>{_esc(t)}</b> &mdash; {_esc(d)}</li>" for t, d in items)
    return _block(
        "Where to go next",
        "The other tabs grade this cohort. This one only describes it.",
        f"<ul class='ovnext'>{lis}</ul>",
    )


def overview_section_html(ov: dict) -> str:
    """The whole overview tab: scanned, cohort, time, trust, and where to go next."""
    if ov.get("empty"):
        return ("<p class='nogap'>Nothing was indexed under this root, so there is no cohort "
                "to describe. Check the path and that the scan can read it.</p>")
    return ("<div class='overview'>"
            + _scanned_block(ov) + _cohort_block(ov) + _time_block(ov)
            + _trust_block(ov) + _next_block() + "</div>")


def overview_styles() -> str:
    """CSS for the overview tab, in the cohort report's own design language."""
    return """
.overview{display:flex;flex-direction:column;gap:22px}
.ovblock{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px 18px 18px}
.ovh{margin:0 0 4px;font-size:13.5px;font-weight:600;color:var(--ink);letter-spacing:.01em}
.ovlede{margin:0 0 14px;font-size:12px;color:var(--muted);line-height:1.6;max-width:78ch}
/* auto-fit rather than a fixed column count: a block with two figures must not leave three
   empty tracks, which is exactly the fault this redesign was called in to fix elsewhere. */
.ovstats{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(215px,1fr))}
.ovstat{background:var(--surface-2);border:1px solid var(--line);
  border-left:3px solid var(--accent-line);border-radius:var(--radius-sm);padding:10px 13px 11px}
.ovstat.wide{grid-column:1/-1}
.ovstat.warn{border-left-color:var(--warn)}
.ovnum{font-family:var(--mono);font-size:19px;font-weight:700;color:var(--ink);line-height:1.25}
.ovstat.warn .ovnum{color:var(--warn)}
.ovto{font-family:var(--sans);font-size:12px;font-weight:400;color:var(--dim)}
.ovlab{font-size:11.5px;font-weight:600;color:var(--muted);margin-top:2px}
.ovnote{margin:6px 0 0;font-size:11.5px;color:var(--dim);line-height:1.55}
.ovtable{margin-top:14px}
.ovtable .ovmod{font-family:var(--mono);font-weight:600;color:var(--ink)}
.ovtable th.ovn,.ovtable td.ovn{text-align:right;white-space:nowrap}
.ovnext{margin:0;padding-left:18px;font-size:12px;color:var(--muted);line-height:1.75}
.ovnext b{color:var(--ink)}
@media (max-width:760px){
  .ovstats{grid-template-columns:1fr}
}
"""
