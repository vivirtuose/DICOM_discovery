"""Completeness as a *readable grid* — the shared renderer for the tab and the standalone page.

Why this module exists: the previous view was a Plotly heatmap of patients x (timepoint x
modality). On the demo cohort two thirds of its cells were grey "not expected" noise, the
actionable MISSING signal was 5 %, the 24 tilted column labels repeated every timepoint once
per modality, and the whole thing shipped as a 4.9 MB file in a palette that belonged to a
different product. None of that is a rendering detail: a physician could not tell, at a
glance, which patient to re-export.

The replacement is plain HTML + CSS, no chart library, nothing fetched at view time:

* one row per patient, **worst-first** (the order ``completeness_grid`` already produces);
* one column per **protocol timepoint** (4 columns instead of 24), each cell holding one
  small chip per modality that is either expected or observed there;
* **"not expected" is rendered as nothing at all** — absence of ink, not a grey box. The
  legend says so once in prose, which is cheaper than paying for it in every cell;
* every chip carries its state three times over — a CSS class, a glyph + word in the text,
  and an ``aria-label`` — so it never depends on colour alone (WCAG 1.4.1), exactly as the
  RT verdict pills in :mod:`DICOM_discovery.report_cohort` already do;
* the palette, the chip grammar and the toolbar are taken from the cohort report, so the two
  views read as one clinical document rather than two tools.

**Research Use Only — not a medical device.**

The same function feeds both outputs (the report's third tab and ``dicom-discovery
completeness``), so the two can never drift apart.
"""
from __future__ import annotations

import html
from typing import Dict, List, Optional

from .completeness import Protocol

RUO_TEXT = "Research Use Only - not a medical device."

#: Which cohort-report verdict colour each completeness state borrows. The mapping is by
#: *meaning*, not by looks: PRESENT is a settled OK; MISSING is the actionable failure, same
#: brick red as INCOMPLETE; UNMAPPED is "can't tell yet", the ochre warning; EXTRA is data
#: outside the protocol's scope, the same neutral slate as NO_RT.
_STATE_VERDICT = {
    "PRESENT": "OK",
    "MISSING": "INCOMPLETE",
    "UNMAPPED": "WARN",
    "EXTRA": "NO_RT",
}

# Glyph + word per state. Written as HTML entities so this source file stays pure ASCII
# while the page still shows a tick and a cross.
_STATE_GLYPH = {"PRESENT": "&#10003;", "MISSING": "&#10007;", "EXTRA": "+", "UNMAPPED": "?"}
_STATE_WORD = {"PRESENT": "present", "MISSING": "missing", "EXTRA": "extra",
               "UNMAPPED": "unmapped"}

#: Legend order: the actionable state first, because that is what the page is for.
_LEGEND = [
    ("MISSING", "expected by the protocol, not found - re-export or chase it"),
    ("PRESENT", "expected and found"),
    ("EXTRA", "found, but the protocol does not ask for it"),
    ("UNMAPPED", "data exists but no study fell inside a protocol window"),
]


def _esc(v) -> str:
    return html.escape(str(v))


def _plural(n: int, word: str) -> str:
    """'1 patient' / '3 patients' — this text is read by clinicians, not by parsers."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _verb(n: int, singular: str, plural: str) -> str:
    """'1 patient has' / '3 patients have' — pairs with :func:`_plural` on the same count."""
    return singular if n == 1 else plural


def _is_fully_unmapped(patient: dict) -> bool:
    """True when this patient hit the ``n_expected == 0`` branch *because* every one of their
    cells is UNMAPPED, rather than because the protocol asked nothing of them.

    Such a patient has data, but none of it could be placed on the protocol timeline (e.g.
    every StudyDate was unparseable) — ``build_completeness`` marks every one of their cells
    UNMAPPED regardless of what the protocol expects there, and ``patient_completeness``
    excludes them entirely (see completeness.py), so they surface with ``n_expected=0`` just
    like a patient the protocol genuinely asks nothing of. The two are not the same claim: one
    is "nothing to grade", the other is "could not be graded". Conflating them let an
    ungradeable patient read as a clean, expected-nothing case.

    The ``n_expected == 0`` guard matters: a patient can have a single UNMAPPED cell alongside
    cells that *do* carry a real verdict (e.g. one MISSING timepoint plus one UNMAPPED one) —
    that patient has a real, nonzero ``n_expected`` and must keep the ordinary "N / M missing"
    badge, not the fully-unmapped one.
    """
    if int(patient.get("n_expected", 0) or 0) != 0:
        return False
    cells = patient.get("cells") or []
    return bool(cells) and all(str(c.get("state")) == "UNMAPPED" for c in cells)


# --------------------------------------------------------------------------- #
# KPI line
# --------------------------------------------------------------------------- #
def _kpi_seg(num, lab: str, title: str, cls: str = "", extra: str = "") -> str:
    lab_cls = f"kpi-lab {cls}" if cls else "kpi-lab"
    return (f"<div class='kpi ckpi{extra}' title='{_esc(title)}'>"
            f"<span class='kpi-num'>{_esc(num)}</span>"
            f"<span class='{lab_cls}'>{_esc(lab)}</span></div>")


def _kpi_line_html(grid: List[dict], kpis: dict) -> str:
    """The headline strip: how many patients are actually complete, and what is most missed.

    ``n_complete`` from the data layer counts a patient with *nothing expected* as complete
    (``n_expected=0`` gives ``pct_complete=100``, numerically identical to a genuinely
    complete patient) — and it counts a fully-UNMAPPED patient (data exists, but none of it
    could be placed on the timeline) the same way, for the same numeric reason. Reporting that
    number verbatim would overstate the cohort, so both kinds of patient are subtracted here:
    the vacuous ones are surfaced as their own segment, the unplaceable ones are not "nothing
    expected" of them either (their row says so — see ``_row_html``) and must not be silently
    read as complete or as vacuous.
    """
    n_patients = int(kpis.get("n_patients", 0) or 0)
    n_unplaceable = sum(1 for p in grid if _is_fully_unmapped(p))
    n_vacuous = sum(1 for p in grid
                    if int(p.get("n_expected", 0) or 0) == 0 and not _is_fully_unmapped(p))
    n_complete = max(0, int(kpis.get("n_complete", 0) or 0) - n_vacuous - n_unplaceable)

    cells = [_kpi_seg(
        f"{n_complete} / {n_patients}", "patients complete",
        f"{n_complete} of {n_patients} patients have every object the protocol expects.",
    )]

    if n_vacuous:
        cells.append(_kpi_seg(
            n_vacuous, "nothing expected",
            f"{_plural(n_vacuous, 'patient')} {_verb(n_vacuous, 'has', 'have')} nothing "
            "expected by this protocol (no protocol window matched their studies). Not "
            "counted as complete: there was nothing to be complete about.",
            cls="dim",
        ))

    gap = kpis.get("worst_gap")
    if gap:
        pair = f"{gap['timepoint']} - {gap['modality']}"
        n = int(gap["n_patients"])
        cells.append(_kpi_seg(
            pair, f"most-missed in {_plural(n, 'patient')}",
            f"Most-missed: {pair} ({_plural(n, 'patient')}). One batch re-export of this "
            "timepoint and modality would close the largest gap in the cohort.",
            cls="act", extra=" kpi-gap",
        ))

    n_unmapped = int(kpis.get("n_unmapped", 0) or 0)
    if n_unmapped > 0:
        cells.append(_kpi_seg(
            n_unmapped, "not placed on the timeline",
            f"Data-quality note: {n_unmapped} timepoint cells could not be placed on the "
            "protocol window - the study dates fall outside every window. Check the dates or "
            "the protocol window; this is not missing patient data.",
            cls="warn",
        ))

    return f"<section class='kpis ckpis' aria-label='completeness summary'>{''.join(cells)}</section>"


# --------------------------------------------------------------------------- #
# Protocol line — what justifies the word "expected"
# --------------------------------------------------------------------------- #
def _protocol_line_html(protocol: Protocol) -> str:
    """Spell out the contract the grid grades against: when each timepoint is, and what it needs.

    Without this line "expected" is an unexplained verdict; with it, a physician can see that
    a MISSING chip means "no MR within 30 days of day 360", not "we lost your scan".
    """
    parts = []
    for tp in protocol.timepoints:
        offset = protocol.offsets.get(tp, 0)
        mods = ", ".join(protocol.required.get(tp, [])) or "nothing"
        parts.append(
            f"<span class='ptp'><b>{_esc(tp)}</b> "
            f"<span class='pwin'>day {int(offset)} +/-{int(protocol.tolerance_days)}</span> "
            f"<span class='pmods'>{_esc(mods)}</span></span>"
        )
    # The separator is a real character, not CSS margin. Margin exists only on screen: copied
    # text, a screen reader and a printed page all ran the last modality of one timepoint into
    # the name of the next ("...RTDOSEM3 day 90..."), which reads as a modality called RTDOSEM3.
    sep = "<span class='psep'> &middot; </span>"
    return (f"<p class='protocol-line'>Protocol {_esc(protocol.name)} - expected at each "
            f"timepoint: {sep.join(parts)}</p>")


# --------------------------------------------------------------------------- #
# Legend
# --------------------------------------------------------------------------- #
def _legend_html() -> str:
    items = "".join(
        f"<span class='leg'><span class='sw sw-{state}'></span>"
        f"<b>{_STATE_GLYPH[state]} {_STATE_WORD[state]}</b> - {_esc(desc)}</span>"
        for state, desc in _LEGEND
    )
    blank = ("<span class='leg'><span class='sw sw-blank'></span>"
             "<b>blank</b> - the protocol expects nothing here</span>")
    return f"<div class='legend clegend'>{items}{blank}</div>"


# --------------------------------------------------------------------------- #
# Grid
# --------------------------------------------------------------------------- #
def _chip_html(timepoint: str, modality: str, state: str) -> str:
    """One chip: modality + glyph, with the state repeated in class, text and aria-label."""
    word = _STATE_WORD.get(state, state.lower())
    glyph = _STATE_GLYPH.get(state, "-")
    label = f"{timepoint} {modality} {word}"
    return (f"<span class='chip chip-{_esc(state)}' data-tp='{_esc(timepoint)}' "
            f"data-mod='{_esc(modality)}' data-state='{_esc(state)}' "
            f"aria-label='{_esc(label)}' title='{_esc(timepoint)} - {_esc(modality)} - {word}'>"
            f"<span class='cmark' aria-hidden='true'>{glyph}</span>"
            f"<span class='clab'>{_esc(modality)}</span>"
            f"<span class='sr'>{word}</span></span>")


def _tp_chip_html(timepoint: str, state: str) -> str:
    """One chip per *timepoint*, used only when every modality at that timepoint shares one
    state (currently: a fully-UNMAPPED patient). ~40 identical per-modality chips saying
    "unmapped" tell a reader nothing that one does not; the state still carries in class,
    text and aria-label, same as :func:`_chip_html`."""
    word = _STATE_WORD.get(state, state.lower())
    glyph = _STATE_GLYPH.get(state, "-")
    label = f"{timepoint} {word}"
    return (f"<span class='chip chip-{_esc(state)}' data-tp='{_esc(timepoint)}' "
            f"data-state='{_esc(state)}' aria-label='{_esc(label)}' "
            f"title='{_esc(timepoint)} - {word}'>"
            f"<span class='cmark' aria-hidden='true'>{glyph}</span>"
            f"<span class='clab'>{_esc(word)}</span>"
            f"<span class='sr'>{word}</span></span>")


def followup_status_html(patient: dict) -> str:
    """The badge saying how much of the protocol this patient is missing.

    Public because the integrity table renders it beside the RT verdict. Protocol follow-up
    and RT-chain integrity are two verdicts about one patient, and someone chasing data wants
    them on one row rather than in two tabs they have to reconcile by hand.

    The binding ruling from the data layer shows up here: a patient the protocol asks nothing
    of comes out numerically identical to a fully complete one (0 missing, 100 %). Saying
    "0 / 0 missing" would read as a clean bill of health, so the badge states the truth
    instead. A fully-UNMAPPED patient hits the same ``n_expected == 0`` branch for a different
    reason - the protocol *did* expect things, none of their studies could be dated onto the
    timeline - so it gets its own wording rather than reading as "nothing expected".
    """
    n_expected = int(patient.get("n_expected", 0) or 0)
    n_missing = int(patient.get("n_missing", 0) or 0)
    if _is_fully_unmapped(patient):
        return "<span class='cstat unmapped'>cannot be placed on the timeline</span>"
    if n_expected == 0:
        return "<span class='cstat none'>nothing expected</span>"
    cls = "cstat" if n_missing == 0 else "cstat bad"
    return f"<span class='{cls}'>{n_missing} / {n_expected} missing</span>"


def followup_cells_html(patient: dict, timepoints: List[str]) -> str:
    """One ``<td>`` per protocol timepoint, holding this patient's chips for it.

    Returns the cells rather than a whole row so the integrity table can splice them into a
    row it owns: one renderer for the chips, two tables that can never draw them differently.
    """
    fully_unmapped = _is_fully_unmapped(patient)
    by_tp = {str(c.get("timepoint")): c for c in patient.get("cells", [])}
    tds = []
    for tp in timepoints:
        cell = by_tp.get(tp) or {}
        items = cell.get("items") or []
        if not items:
            # Absence of ink: the old grey "N/A" box is exactly what this replaces.
            tds.append("<td class='ccell empty'></td>")
            continue
        if fully_unmapped:
            # Every item in this cell shares one state (UNMAPPED) by construction, so one chip
            # says the same thing ~len(items) chips did.
            state = str(cell.get("state") or "UNMAPPED")
            tds.append(f"<td class='ccell' data-tp='{_esc(tp)}'>{_tp_chip_html(tp, state)}</td>")
            continue
        chips = "".join(_chip_html(tp, str(it["modality"]), str(it["state"])) for it in items)
        tds.append(f"<td class='ccell' data-tp='{_esc(tp)}'>{chips}</td>")
    return "".join(tds)


def followup_inline_html(patient: dict, timepoints: List[str]) -> str:
    """The whole follow-up, as one inline cell for the integrity table.

    The standalone grid gives each timepoint its own column; the integrity table cannot afford
    four columns beside the verdict, the chain and the action, so the timepoints are grouped
    inside a single cell instead - each group labelled, so a chip still says *when* as well as
    *what*. Same chips, same states, same colours as the grid: one renderer, two layouts.
    """
    fully_unmapped = _is_fully_unmapped(patient)
    by_tp = {str(c.get("timepoint")): c for c in patient.get("cells", [])}
    groups = []
    for tp in timepoints:
        items = (by_tp.get(tp) or {}).get("items") or []
        if not items:
            continue          # absence of ink, exactly as in the grid
        if fully_unmapped:
            state = str((by_tp.get(tp) or {}).get("state") or "UNMAPPED")
            chips = _tp_chip_html(tp, state)
        else:
            chips = "".join(_chip_html(tp, str(it["modality"]), str(it["state"]))
                            for it in items)
        groups.append(f"<span class='fu-grp'><span class='fu-lab'>{_esc(tp)}</span>{chips}</span>")
    if not groups:
        return "<span class='dim'>&mdash;</span>"
    return "".join(groups)


def followup_export_text(patient: dict) -> str:
    """What the follow-up column contributes to a CSV row.

    A generic table export reads cell text, and a cell of chips reads as a run of glyphs and
    screen-reader words glued together. This names the missing pairs instead, so the exported
    file says which re-export closes the gap - the only thing the column is for.
    """
    if _is_fully_unmapped(patient):
        return "cannot be placed on the timeline"
    n_expected = int(patient.get("n_expected", 0) or 0)
    if n_expected == 0:
        return "nothing expected"
    missing = [f"{c['timepoint']} {it['modality']}"
               for c in patient.get("cells", [])
               for it in (c.get("items") or []) if it.get("state") == "MISSING"]
    if not missing:
        return "complete"
    return f"{len(missing)}/{n_expected} missing: " + ", ".join(missing)


def followup_detail_html(patient: dict, timepoints: List[str]) -> str:
    """The per-timepoint, per-modality breakdown, for a patient's drill-down panel.

    The summary row shows one chip per modality already; this spells out the same states as
    words, timepoint by timepoint, because a drill-down is where someone reads carefully
    rather than scans.
    """
    by_tp = {str(c.get("timepoint")): c for c in patient.get("cells", [])}
    lines = []
    for tp in timepoints:
        items = (by_tp.get(tp) or {}).get("items") or []
        if not items:
            lines.append(f"<li><span class='fu-tp mono'>{_esc(tp)}</span>"
                         "<span class='dim'>nothing expected</span></li>")
            continue
        words = ", ".join(
            f"<span class='fu-item fu-{_esc(str(it['state']))}'>{_esc(str(it['modality']))} "
            f"{_STATE_WORD.get(str(it['state']), str(it['state']).lower())}</span>"
            for it in items
        )
        lines.append(f"<li><span class='fu-tp mono'>{_esc(tp)}</span>{words}</li>")
    return f"<ul class='fu-detail'>{''.join(lines)}</ul>"


def _row_html(patient: dict, timepoints: List[str]) -> str:
    pid = str(patient.get("patient", ""))
    n_expected = int(patient.get("n_expected", 0) or 0)
    n_missing = int(patient.get("n_missing", 0) or 0)
    # See _grid_html: the slack column keeps the timepoint columns at content width.
    tds = followup_cells_html(patient, timepoints) + "<td class='cslack'></td>"
    return (f"<tr class='crow' data-pid='{_esc(pid)}' data-missing='{n_missing}' "
            f"data-expected='{n_expected}' data-filter='{_esc(pid.lower())}'>"
            f"<th class='cc-pid' scope='row'><span class='mono pid'>{_esc(pid)}</span>"
            f"{followup_status_html(patient)}</th>{tds}</tr>")


def _grid_html(grid: List[dict], protocol: Protocol) -> str:
    """The per-patient drill-down.

    The trailing ``cslack`` column is load-bearing, not decoration. ``.grid`` is ``width:100%``
    and this protocol has one wide timepoint (baseline, five modalities) against three narrow
    ones (a single MR each): the browser handed each narrow column a third of ~740 leftover
    pixels, so three chips sat marooned in half a page of white. The slack column takes
    ``width:100%`` and absorbs that leftover, letting the real columns size to their content
    while the table still spans its box. It holds nothing and carries no chip, so the CSV
    export (which walks ``.chip``) never sees it.
    """
    heads = "".join(f"<th class='ctp' scope='col'>{_esc(tp)}</th>" for tp in protocol.timepoints)
    rows = "".join(_row_html(p, list(protocol.timepoints)) for p in grid)
    return (f"<div class='cgrid-scroll'><table class='grid cgrid' id='comp-grid'>"
            f"<thead><tr><th class='cc-pid' scope='col'>Patient</th>{heads}"
            f"<th class='cslack'></th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")


#: Names shown inline in a gap row before the rest move into a disclosure. Fifteen ids still
#: read as a list rather than a wall; past that the row would set the height of the whole
#: table for one crowded gap.
_WHO_LIMIT = 15


def gap_table_html(gaps: List[dict]) -> str:
    """The headline: one row per (timepoint, modality) that at least one patient is missing.

    This is the shape of the action. A QC round does not re-export a patient, it re-exports a
    timepoint and a modality for whoever is missing it, so that pair - and how many patients
    ride on it - is what belongs above the fold. The per-patient grid answers the follow-up
    question, which is why it now sits behind a drill-down.
    """
    if not gaps:
        return ("<p class='nogap'>No gap: every expected object is present.</p>")

    rows = []
    for gap in gaps:
        patients = list(gap.get("patients") or [])
        who = ", ".join(_esc(p) for p in patients[:_WHO_LIMIT])
        rest = patients[_WHO_LIMIT:]
        if rest:
            # A native disclosure, not a tooltip: the names that did not fit have to be
            # reachable by click and by keyboard, selectable and copyable. A hover title is
            # none of those, and a truncated cell must never be the only place a patient
            # existed.
            who += (f"<details class='gmore'><summary>+{len(rest)} more</summary>"
                    f"<div class='gmore-list mono'>{', '.join(_esc(p) for p in rest)}</div>"
                    f"</details>")
        rows.append(
            f"<tr class='grow'><td class='gtp'>{_esc(gap['timepoint'])}</td>"
            f"<td class='gmod'>{_esc(gap['modality'])}</td>"
            f"<td class='gnum mono'>{int(gap['n_patients'])}</td>"
            f"<td class='gwho'>{who}</td></tr>"
        )
    return (f"<table class='grid gaptable' id='comp-gaps'>"
            f"<thead><tr><th>Timepoint</th><th>Modality</th>"
            f"<th class='gnum'>Patients missing</th><th>Who</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def _toolbar_html() -> str:
    return ("<div class='toolbar'>"
            "<input type='search' id='comp-filter' class='filter' placeholder='Filter patients&hellip;' "
            "aria-label='filter completeness grid'>"
            "<button type='button' class='btn' id='comp-only-missing' aria-pressed='false'>"
            "Only incomplete</button>"
            "<button type='button' class='btn' id='comp-export'>Export CSV</button>"
            "<span class='hint'>Worst patients first; blank = nothing expected.</span></div>")


def completeness_section_html(grid: List[dict], kpis: dict, protocol: Protocol,
                              gaps: List[dict]) -> str:
    """Render the whole completeness section, aggregate first and per-patient second.

    Order is the argument this function makes: KPI line, protocol line, the cohort gap table,
    and only then the per-patient grid, collapsed inside a native ``<details>``. What
    triggers an action is "chase the M6 MR for 3 patients" - one line. A 98 x 4 wall of chips
    to carry that sentence is out of proportion, so it becomes the drill-down.

    ``<details>`` is deliberately native: no new JS, and keyboard and screen-reader behaviour
    come for free.

    Pure HTML - no chart library, no external resource, no per-row ``<style>``. The caller
    supplies the page's stylesheet (:func:`completeness_styles`) and script
    (:func:`completeness_script`) exactly once.
    """
    head = _kpi_line_html(grid, kpis) + _protocol_line_html(protocol)
    if not grid:
        # Not "no gap" - nothing was gradeable at all, which is a different statement.
        return (f"<section class='comp'>{head}"
                "<p class='note'>No completeness data - no patient could be graded against "
                "this protocol. Check that the index found dated studies.</p></section>")
    drill = (f"<details class='comp-detail'>"
             f"<summary class='comp-summary'>Per-patient detail "
             f"({_plural(len(grid), 'patient')})</summary>"
             f"{_toolbar_html()}{_legend_html()}{_grid_html(grid, protocol)}"
             f"</details>")
    return f"<section class='comp'>{head}{gap_table_html(gaps)}{drill}</section>"


# --------------------------------------------------------------------------- #
# Styles / script (shared by the tab and the standalone page)
# --------------------------------------------------------------------------- #
def completeness_styles() -> str:
    """CSS for the completeness section, in the cohort report's own design language."""
    # Local import on purpose: report_cohort imports *this* module to build its third tab, so
    # importing it at module level would close the cycle. Sourcing the palette from the one
    # place that defines it matters more than import tidiness — the two views must not drift.
    from .report_cohort import VERDICT_COLORS

    swatches = "\n".join(
        f".sw-{state}{{background:color-mix(in srgb,{VERDICT_COLORS[verdict]} 22%,var(--mix-up));"
        f"border-color:color-mix(in srgb,{VERDICT_COLORS[verdict]} 45%,transparent)}}\n"
        f".chip-{state}{{--chip:{VERDICT_COLORS[verdict]}}}"
        for state, verdict in _STATE_VERDICT.items()
    )
    return """
/* ---- completeness: KPI strip variant (left-aligned, no pushed-right last cell) ---- */
.ckpis{margin-bottom:14px}
.ckpis .kpi:last-child{margin-left:0;text-align:left;align-items:flex-start}
.ckpis .kpi-gap{min-width:200px}
.ckpis .kpi-gap .kpi-num{font-size:15px;font-family:var(--mono);padding-top:4px}

/* ---- the protocol line: what justifies the word "expected" ---- */
.protocol-line{
  color:var(--muted);font-size:12px;line-height:1.9;margin:0 0 16px;
  padding:9px 13px;background:var(--surface-2);
  border:1px solid var(--line);border-left:3px solid var(--accent-line);
  border-radius:var(--radius-sm);
}
.protocol-line .ptp{white-space:nowrap}
.protocol-line .psep{color:var(--line-strong);padding:0 9px}
.protocol-line .ptp b{color:var(--ink);font-weight:600}
.protocol-line .pwin{font-family:var(--mono);font-size:11px;color:var(--dim)}
.protocol-line .pmods{font-family:var(--mono);font-size:11px;color:var(--muted)}

/* ---- the gap table: the headline, so it gets the weight the grid used to have ---- */
.gaptable{margin-bottom:20px}
.gaptable td{padding:9px 14px}
.gaptable .gtp{font-family:var(--mono);font-weight:600;color:var(--ink);white-space:nowrap}
.gaptable .gmod{font-family:var(--mono);color:var(--ink);white-space:nowrap}
.gaptable th.gnum,.gaptable td.gnum{text-align:right;white-space:nowrap}
.gaptable td.gnum{font-weight:700;color:var(--incomplete)}
.gaptable .gwho{font-family:var(--mono);font-size:11.5px;color:var(--muted);line-height:1.6}
.gmore{display:inline-block;margin-left:4px;font-family:var(--sans)}
.gmore>summary{cursor:pointer;display:inline-block;list-style:none;font-size:11px;
  font-style:italic;color:var(--accent);padding:0 5px;border:1px solid var(--accent-line);
  border-radius:9px;background:var(--accent-soft)}
.gmore>summary::-webkit-details-marker{display:none}
.gmore>summary:focus-visible{outline:none;box-shadow:0 0 0 2px var(--accent-soft)}
.gmore[open]>summary{color:var(--muted)}
.gmore-list{display:block;margin-top:6px;padding:7px 9px;background:var(--surface-2);
  border:1px solid var(--line);border-radius:var(--radius-sm);font-size:11px;
  color:var(--muted);line-height:1.7;max-height:190px;overflow:auto}
/* A clean cohort is a result, not an empty table — it reads as a sentence. */
.nogap{
  color:var(--ok);font-size:13px;margin:0 0 20px;padding:11px 14px;
  background:color-mix(in srgb,var(--ok) 10%,var(--mix-up));
  border:1px solid color-mix(in srgb,var(--ok) 34%,transparent);border-radius:var(--radius);
}

/* ---- the per-patient drill-down (native <details>, no JS) ---- */
.comp-detail{
  background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:0 16px;
}
.comp-summary{
  cursor:pointer;padding:11px 2px;font-size:12.5px;font-weight:600;color:var(--accent);
  list-style-position:outside;
}
.comp-summary:focus-visible{outline:none;box-shadow:0 0 0 2px var(--accent-soft)}
.comp-detail[open] .comp-summary{border-bottom:1px solid var(--line);margin-bottom:14px}
.comp-detail>*:last-child{margin-bottom:16px}

/* ---- legend ----
   No `.clegend .sw` rule here: a descendant selector (0,2,0) would out-specify every
   `.sw-*` variant below (0,1,0) and silently repaint all five swatches the same grey. The
   base `.sw` already supplies the border; the variants only need to tie with it and come
   later in the stylesheet, which is why the page emits the base CSS first. */
.clegend{color:var(--muted)}
.sw-blank{background:transparent;border-style:dashed}

/* ---- the grid: sticky header + sticky patient column, compact rows ---- */
.cgrid-scroll{
  max-height:74vh;overflow:auto;background:var(--surface);
  border:1px solid var(--line);border-radius:var(--radius);
}
/* The base .grid rounds its corners with overflow:hidden; that clips position:sticky, so the
   header row would scroll away at 98 patients. The wrapper owns the border and the radius
   now, and the table hands its overflow back. */
.cgrid{border:none;border-radius:0;overflow:visible}
.cgrid thead th{position:sticky;top:0;z-index:3}
.cgrid thead th.cc-pid{left:0;z-index:4}
.cgrid th.ctp{font-family:var(--mono);font-size:12px;color:var(--ink);font-weight:600}
.cgrid td,.cgrid tbody th{padding:5px 10px;vertical-align:middle}
.cgrid tbody th.cc-pid{
  position:sticky;left:0;z-index:2;background:var(--surface);
  text-align:left;font-weight:400;border-bottom:1px solid var(--line);
  border-right:1px solid var(--line);white-space:nowrap;
}
.cgrid tbody tr:hover th.cc-pid{background:var(--surface-2)}
.cgrid .pid{font-weight:600;color:var(--ink);margin-right:9px}
.cstat{font-size:11px;color:var(--dim);font-family:var(--mono)}
.cstat.bad{color:var(--incomplete);font-weight:600}
/* "nothing expected" must not read as a clean 100% — it is a neutral statement of fact. */
.cstat.none{
  font-family:var(--sans);font-style:italic;color:var(--muted);
  border:1px dashed var(--line-strong);border-radius:var(--radius-sm);padding:0 6px;
}
/* "cannot be placed on the timeline" is a data-quality flag, not a clean bill of health —
   same ochre as the UNMAPPED chip/verdict (WARN), so the row and its chips agree. */
.cstat.unmapped{
  font-family:var(--sans);font-style:italic;color:var(--warn);
  border:1px dashed color-mix(in srgb,var(--warn) 45%,transparent);
  border-radius:var(--radius-sm);padding:0 6px;
}
.ccell{white-space:nowrap}
.ccell.empty{background:none}
/* Takes every leftover pixel so the timepoint columns stay at content width — see _grid_html. */
.cgrid .cslack{width:100%;padding:0;border-bottom:1px solid var(--line)}

/* ---- follow-up, inline in the integrity table ----
   The grid can spend a column per timepoint; the integrity row cannot, so the timepoints are
   grouped inside one cell and each group carries its own label. Without the label a chip says
   "MR missing" without saying which visit, which is the half that decides what to re-export. */
.c-fu{white-space:normal;min-width:230px}
.fu-grp{display:inline-flex;align-items:center;gap:3px;margin:2px 10px 2px 0;
  padding-left:7px;border-left:2px solid var(--line)}
.fu-lab{font-family:var(--mono);font-size:9.5px;font-weight:700;letter-spacing:.04em;
  color:var(--dim);text-transform:uppercase;margin-right:2px}
.fu-detail{list-style:none;margin:6px 0 0;padding:0;font-size:11.5px;line-height:1.9}
.fu-detail li{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.fu-tp{min-width:64px;font-weight:600;color:var(--ink);font-size:11px}
.fu-item{color:var(--muted)}
.fu-MISSING{color:var(--incomplete);font-weight:600}
.fu-UNMAPPED{color:var(--warn)}

/* ---- the chip: modality + glyph, never colour alone ---- */
.chip{
  display:inline-flex;align-items:center;gap:4px;margin:1px 3px 1px 0;
  font-size:10.5px;font-weight:600;letter-spacing:.01em;
  color:color-mix(in srgb,var(--chip) 82%,var(--mix-down));
  background:color-mix(in srgb,var(--chip) 13%,var(--mix-up));
  border:1px solid color-mix(in srgb,var(--chip) 30%,transparent);
  padding:1px 6px;border-radius:var(--radius-sm);white-space:nowrap;
}
.chip .cmark{font-size:11px;line-height:1}
.chip .clab{font-family:var(--mono);font-size:10px}
/* MISSING is the one state that must survive a squint across 98 rows. */
.chip-MISSING{border-width:1px;border-style:solid;font-weight:700}
.chip-MISSING .cmark{font-weight:700}
/* EXTRA and UNMAPPED are informational: outlined, not filled (same grammar as .pcell.off). */
.chip-EXTRA,.chip-UNMAPPED{background:var(--surface);border-style:dashed}

/* Screen-reader-only text: the state word lives in the DOM for assistive tech even though
   the glyph carries it visually. */
.sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
  clip:rect(0 0 0 0);white-space:nowrap;border:0}

""" + swatches + """

@media (max-width:760px){
  .cgrid-scroll{max-height:none}
  .cgrid tbody th.cc-pid,.cgrid thead th.cc-pid{position:static}
}
"""


def _csv_rows_js() -> str:
    """The pure, DOM-free half of the CSV export, kept separate so it can be run in a test.

    Everything that decides *what lands in the file* lives here and takes plain data; the
    click handler only walks the DOM to build that data. Splitting the two is what makes the
    "a patient must never silently vanish from the export" rule testable rather than asserted
    by reading the source.
    """
    return r"""
  function compCsvRows(entries){
    var rows = [['patient','timepoint','modality','state']];
    entries.forEach(function(e){
      // A patient with no chip at all is not a patient with no data: it is a patient the
      // protocol expects nothing of. Walking only the chips dropped them from the file
      // entirely, so a reader reconciling the CSV against the cohort would conclude they do
      // not exist. They get one row that says exactly what is true about them.
      if(!e.chips || !e.chips.length){
        rows.push([e.patient, '', '', 'nothing expected']);
        return;
      }
      e.chips.forEach(function(c){
        rows.push([e.patient, c.timepoint, c.modality, c.state]);
      });
    });
    return rows;
  }
"""


def completeness_script() -> str:
    """Vanilla JS for the grid: patient filter, only-incomplete toggle, CSV export.

    Same idioms as the cohort report's own script (shared state object, ``hidden`` toggling,
    Blob download with deferred cleanup) so the two behave identically.
    """
    return r"""
(function(){
  "use strict";
""" + _csv_rows_js() + r"""
  var grid = document.getElementById('comp-grid');
  if(!grid) return;
  var cState = {q:'', onlyMissing:false};
  function applyCompFilter(){
    grid.querySelectorAll('tbody tr.crow').forEach(function(tr){
      var hay = tr.getAttribute('data-filter') || '';
      var miss = parseInt(tr.getAttribute('data-missing') || '0', 10);
      var show = hay.indexOf(cState.q) !== -1 && (!cState.onlyMissing || miss > 0);
      tr.hidden = !show;
    });
  }
  var cFilter = document.getElementById('comp-filter');
  if(cFilter){
    cFilter.addEventListener('input', function(){
      cState.q = cFilter.value.trim().toLowerCase();
      applyCompFilter();
    });
  }
  var cToggle = document.getElementById('comp-only-missing');
  if(cToggle){
    cToggle.addEventListener('click', function(){
      cState.onlyMissing = !cState.onlyMissing;
      cToggle.setAttribute('aria-pressed', cState.onlyMissing ? 'true' : 'false');
      applyCompFilter();
    });
  }
  var cExport = document.getElementById('comp-export');
  if(cExport){
    cExport.addEventListener('click', function(){
      // Walk the DOM into plain data, then let compCsvRows decide what lands in the file.
      // Values come from the chips' data attributes, not their text, so the glyphs and the
      // screen-reader words never reach the CSV.
      var entries = [];
      grid.querySelectorAll('tbody tr.crow').forEach(function(tr){
        if(tr.hidden) return;
        var chips = [];
        tr.querySelectorAll('.chip').forEach(function(c){
          chips.push({timepoint: c.getAttribute('data-tp') || '',
                      modality: c.getAttribute('data-mod') || '',
                      state: c.getAttribute('data-state') || ''});
        });
        entries.push({patient: tr.getAttribute('data-pid') || '', chips: chips});
      });
      var rows = compCsvRows(entries);
      var csv = rows.map(function(r){
        return r.map(function(c){ return '"' + String(c).replace(/"/g,'""') + '"'; }).join(',');
      }).join('\n');
      var blob = new Blob([csv], {type:'text/csv;charset=utf-8;'});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url; a.download = 'completeness.csv'; a.rel = 'noopener';
      document.body.appendChild(a); a.click();
      setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
    });
  }
})();
"""


# --------------------------------------------------------------------------- #
# Standalone page (the `dicom-discovery completeness` command)
# --------------------------------------------------------------------------- #
def completeness_page_html(grid: List[dict], kpis: dict, protocol: Protocol,
                           gaps: List[dict], title: Optional[str] = None,
                           manifest: Optional[Dict[str, object]] = None) -> str:
    """A complete, self-contained HTML page wrapping :func:`completeness_section_html`.

    Same section, same stylesheet, same script as the cohort report's third tab — the
    standalone command is a different *frame* around identical content, never a second
    implementation.
    """
    from .report_cohort import _styles  # local import: see completeness_styles()

    title = title or f"Cohort completeness - {protocol.name}"
    root = _esc((manifest or {}).get("root", "")) if manifest else ""
    generated = _esc((manifest or {}).get("generated_utc", "")) if manifest else ""
    meta = ""
    if root or generated:
        meta = (f"<div class='manifest'><span class='manifest-item'><span class='mk'>root</span>"
                f"<span class='mv'>{root}</span></span>"
                f"<span class='manifest-item'><span class='mk'>generated</span>"
                f"<span class='mv'>{generated}</span></span></div>")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>{_styles()}{completeness_styles()}</style>
</head><body>
<header class="topbar">
  <div class="brand">
    <span class="title">DICOM<span class="thin">_discovery</span></span>
    <span class="subtitle">cohort completeness against protocol {_esc(protocol.name)}</span>
  </div>
  <div class="ruo" role="note" aria-label="usage restriction">{_esc(RUO_TEXT)}</div>
  {meta}
</header>
<main>
{completeness_section_html(grid, kpis, protocol, gaps)}
</main>
<footer>
  <b>{_esc(RUO_TEXT)}</b><br>
  Header-only cohort QC over synthetic or de-identified DICOM. Not a clinical safety check.
  Generated by DICOM_discovery.
</footer>
<script>{completeness_script()}</script>
</body></html>"""
