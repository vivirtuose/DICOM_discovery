# Plan — redesign the completeness view (2026-09-23)

**Problem, measured on the demo cohort.** The completeness map answers a question nobody asks.
It renders patients x (timepoint x modality): **66 %** of its cells are grey "not expected",
the actionable MISSING signal is **5 %**, the axis carries 24 flat labels tilted -45 degrees
with each timepoint repeated six times, and at 98 patients it becomes 2 352 cells in a
4 120 px figure. Patients are ordered alphabetically rather than worst-first, state is encoded
by colour alone (a defect the RT table in the same product already fixed), and the whole thing
ships as a separate 4.9 MB file beside the report a clinician actually opens.

**Shape of the fix.** Ask the clinician's question instead: *which patients are unusable, and
what is missing?* One row per patient (worst first), one column per protocol timepoint, each
cell a small strip of labelled chips — the same visual grammar as the RT chain strip. Nothing
is drawn where nothing is expected. The view moves into the cohort report as a third tab, and
the standalone command renders the same component.

## Global constraints
- Test-driven: the test comes first and must fail for the right reason before implementation.
- Python 3.9 compatible; no new dependencies; nothing fetched at render time.
- User-visible strings in English, ASCII (they also land in the CSV export).
- One implementation of the grid, shared by the tab and the standalone page.
- The whole suite stays green; `ruff check src synth tests bench` stays clean.

## Tasks
1. **Data layer** (`completeness.py`): `completeness_grid(long_df)` — per-patient rows ordered
   worst-first with per-timepoint cells and their items — and `completeness_kpis(long_df)` —
   cohort counts plus the most-missed (timepoint, modality) pair. Pure functions, no HTML.
2. **Rendering** (`report_completeness.py`, `report_cohort.py`, `report_map.py`): the grid, the
   KPI line, the protocol line, filter / "only incomplete" toggle / CSV export; wired as a third
   tab and reused by the standalone page, which loses its Plotly payload.
3. **Docs and version**: CHANGELOG 0.12.0, READMEs, version bump across the deployment files.

## Deliberately out of scope
The "real timeline vs protocol window" view (study dates on a day axis with the tolerance bands
shaded) — it answers a different question (visit drift) and deserves its own plan.
