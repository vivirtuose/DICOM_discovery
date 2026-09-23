"""Tests for report_cohort.py data-assembly layer (Task 1 of Tier 2).

Tests cover the six pure data-assembly functions.  Each test builds real cohort
frames from the session fixtures defined in conftest.py (no hand-mocked DataFrames)
so the assertions pin real behaviour.

HTML rendering (Task 2) and CLI wiring (Task 3) are out of scope here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import (  # noqa: E402
    DEFAULT_PROTOCOL,
    build_completeness,
    patient_completeness,
)
from DICOM_discovery.report_cohort import (  # noqa: E402
    VERDICT_ORDER,
    build_kpis,
    cohort_pct_complete,
    completeness_rows,
    rollup_rows,
    study_findings,
    verdict_counts,
)
from DICOM_discovery.rt_integrity import (  # noqa: E402
    build_rt_integrity,
    build_rt_rollup,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _make_rollup(cohort):
    """Return rollup_df from the synthetic RT cohort fixture."""
    _df, idx, _truth = cohort
    return build_rt_rollup(idx.table)


def _make_study_df(cohort):
    """Return study_df (per-study integrity) from the synthetic RT cohort fixture."""
    _df, idx, _truth = cohort
    return build_rt_integrity(idx.table)


def _make_comp_long(longitudinal):
    """Return comp_long from the longitudinal fixture."""
    _root, idx = longitudinal
    _state, _hover, long_df = build_completeness(idx.table, DEFAULT_PROTOCOL)
    return long_df


# --------------------------------------------------------------------------- #
# verdict_counts
# --------------------------------------------------------------------------- #

class TestVerdictCounts:
    def test_always_returns_all_four_keys_on_none(self):
        """Even with None input the four verdict keys must be present (value 0)."""
        counts = verdict_counts(None)
        assert set(counts.keys()) == set(VERDICT_ORDER)
        assert all(v == 0 for v in counts.values())

    def test_always_returns_all_four_keys_on_empty(self):
        counts = verdict_counts(pd.DataFrame())
        assert set(counts.keys()) == set(VERDICT_ORDER)
        assert all(v == 0 for v in counts.values())

    def test_counts_real_verdicts(self, cohort):
        rollup_df = _make_rollup(cohort)
        counts = verdict_counts(rollup_df)
        # All four keys present
        assert set(counts.keys()) == set(VERDICT_ORDER)
        # Total across keys equals number of patients
        assert sum(counts.values()) == len(rollup_df)
        # All values are non-negative ints
        assert all(isinstance(v, int) and v >= 0 for v in counts.values())

    def test_missing_verdict_defaults_to_zero(self, cohort):
        """A verdict absent from the data still gets key 0, never KeyError."""
        rollup_df = _make_rollup(cohort)
        counts = verdict_counts(rollup_df)
        for key in VERDICT_ORDER:
            assert key in counts


# --------------------------------------------------------------------------- #
# cohort_pct_complete
# --------------------------------------------------------------------------- #

class TestCohortPctComplete:
    def test_none_on_none_input(self):
        """cohort_pct_complete(None) must return None without raising AttributeError."""
        assert cohort_pct_complete(None) is None

    def test_none_for_empty_df(self):
        empty = pd.DataFrame(columns=["patient", "n_expected", "n_present", "n_missing",
                                      "pct_complete", "timepoint", "modality",
                                      "expected", "observed", "state"])
        result = cohort_pct_complete(empty)
        assert result is None

    def test_returns_float_for_longitudinal(self, longitudinal):
        long_df = _make_comp_long(longitudinal)
        pct = cohort_pct_complete(long_df)
        assert isinstance(pct, float)
        assert 0.0 <= pct <= 100.0

    def test_l001_pulls_mean_toward_100(self, longitudinal):
        """L001 is 100% complete — including it in the cohort must keep pct > 0."""
        long_df = _make_comp_long(longitudinal)
        pct = cohort_pct_complete(long_df)
        assert pct is not None and pct > 0.0


# --------------------------------------------------------------------------- #
# build_kpis
# --------------------------------------------------------------------------- #

class TestBuildKpis:
    def test_keys_present(self, cohort, longitudinal):
        rollup_df = _make_rollup(cohort)
        long_df = _make_comp_long(longitudinal)
        kpis = build_kpis(rollup_df, long_df)
        assert set(kpis.keys()) == {"n_patients", "verdicts", "pct_complete"}

    def test_n_patients_matches_rollup(self, cohort, longitudinal):
        rollup_df = _make_rollup(cohort)
        long_df = _make_comp_long(longitudinal)
        kpis = build_kpis(rollup_df, long_df)
        assert kpis["n_patients"] == len(rollup_df)

    def test_verdicts_is_dict_with_all_keys(self, cohort, longitudinal):
        rollup_df = _make_rollup(cohort)
        long_df = _make_comp_long(longitudinal)
        kpis = build_kpis(rollup_df, long_df)
        assert set(kpis["verdicts"].keys()) == set(VERDICT_ORDER)

    def test_pct_complete_type(self, cohort, longitudinal):
        rollup_df = _make_rollup(cohort)
        long_df = _make_comp_long(longitudinal)
        kpis = build_kpis(rollup_df, long_df)
        # pct_complete is float or None
        assert kpis["pct_complete"] is None or isinstance(kpis["pct_complete"], float)

    def test_empty_rollup_zero_patients(self, longitudinal):
        long_df = _make_comp_long(longitudinal)
        kpis = build_kpis(pd.DataFrame(), long_df)
        assert kpis["n_patients"] == 0


# --------------------------------------------------------------------------- #
# rollup_rows
# --------------------------------------------------------------------------- #

class TestRollupRows:
    def test_empty_df_returns_empty_list(self):
        assert rollup_rows(pd.DataFrame()) == []
        assert rollup_rows(None) == []

    def test_one_dict_per_patient(self, cohort):
        rollup_df = _make_rollup(cohort)
        rows = rollup_rows(rollup_df)
        assert len(rows) == len(rollup_df)

    def test_required_keys_present(self, cohort):
        rollup_df = _make_rollup(cohort)
        rows = rollup_rows(rollup_df)
        required = {"patient_id", "rt_status", "n_studies", "n_rt_studies",
                    "fragmented", "chain", "targets"}
        for row in rows:
            assert required.issubset(set(row.keys())), f"Missing keys in row: {row}"

    def test_chain_is_list_of_four_bools(self, cohort):
        rollup_df = _make_rollup(cohort)
        rows = rollup_rows(rollup_df)
        for row in rows:
            assert isinstance(row["chain"], list)
            assert len(row["chain"]) == 4
            assert all(isinstance(b, bool) for b in row["chain"])

    def test_targets_has_gtv_ctv_ptv(self, cohort):
        rollup_df = _make_rollup(cohort)
        rows = rollup_rows(rollup_df)
        for row in rows:
            assert set(row["targets"].keys()) == {"GTV", "CTV", "PTV"}

    def test_rt_status_is_string(self, cohort):
        rollup_df = _make_rollup(cohort)
        rows = rollup_rows(rollup_df)
        for row in rows:
            assert isinstance(row["rt_status"], str)


# --------------------------------------------------------------------------- #
# study_findings
# --------------------------------------------------------------------------- #

class TestStudyFindings:
    def test_empty_df_returns_empty_dict(self):
        assert study_findings(pd.DataFrame()) == {}
        assert study_findings(None) == {}

    def test_keys_are_patient_ids(self, cohort):
        study_df = _make_study_df(cohort)
        findings = study_findings(study_df)
        rollup_df = _make_rollup(cohort)
        expected_pids = set(rollup_df["patient_id"].astype(str))
        assert set(findings.keys()) == expected_pids

    def test_each_value_is_list_of_dicts(self, cohort):
        study_df = _make_study_df(cohort)
        findings = study_findings(study_df)
        for pid, studies in findings.items():
            assert isinstance(studies, list), f"patient {pid}: value is not a list"
            for s in studies:
                assert isinstance(s, dict), f"patient {pid}: study entry is not a dict"

    def test_study_entry_has_required_keys(self, cohort):
        study_df = _make_study_df(cohort)
        findings = study_findings(study_df)
        required = {"study_date", "rt_status", "n_roi", "findings"}
        for pid, studies in findings.items():
            for s in studies:
                assert required.issubset(set(s.keys())), (
                    f"patient {pid}: missing keys {required - set(s.keys())}"
                )

    def test_findings_field_is_list_of_dicts(self, cohort):
        study_df = _make_study_df(cohort)
        findings = study_findings(study_df)
        for _pid, studies in findings.items():
            for s in studies:
                assert isinstance(s["findings"], list)
                for f in s["findings"]:
                    assert isinstance(f, dict)
                    assert set(f.keys()) == {"severity", "confidence", "text"}

    def test_findings_parsed_correctly_for_formatted_entry(self):
        """Unit test: a manually-crafted findings string parses correctly."""
        row = {
            "patient_id": "TEST001",
            "study_date": "20230101",
            "rt_status": "WARN",
            "n_roi": 3,
            "findings": "[WARN/HIGH] ROI_MISSING: GTV not found ; [INFO/LOW] DOSE_UNIT: cGy",
        }
        df = pd.DataFrame([row])
        result = study_findings(df)
        assert "TEST001" in result
        studies = result["TEST001"]
        assert len(studies) == 1
        parsed = studies[0]["findings"]
        assert len(parsed) == 2
        assert parsed[0]["severity"] == "WARN"
        assert parsed[0]["confidence"] == "HIGH"
        assert "ROI_MISSING" in parsed[0]["text"]
        assert parsed[1]["severity"] == "INFO"


# --------------------------------------------------------------------------- #
# completeness_rows
# --------------------------------------------------------------------------- #

class TestCompletenessRows:
    def test_empty_df_returns_empty_list(self):
        empty = pd.DataFrame(columns=["patient", "timepoint", "modality",
                                      "expected", "observed", "state"])
        assert completeness_rows(empty) == []

    def test_one_dict_per_mappable_patient(self, longitudinal):
        long_df = _make_comp_long(longitudinal)
        rows = completeness_rows(long_df)
        summary = patient_completeness(long_df)
        assert len(rows) == len(summary)

    def test_required_keys_present(self, longitudinal):
        long_df = _make_comp_long(longitudinal)
        rows = completeness_rows(long_df)
        required = {"patient", "n_expected", "n_present", "n_missing", "pct_complete"}
        for row in rows:
            assert required.issubset(set(row.keys()))

    def test_l001_is_100_pct(self, longitudinal):
        long_df = _make_comp_long(longitudinal)
        rows = completeness_rows(long_df)
        l001 = [r for r in rows if r["patient"] == "L001"]
        assert l001, "L001 not found in completeness_rows output"
        assert l001[0]["pct_complete"] == 100.0

    def test_l002_has_missing(self, longitudinal):
        """L002 has M6/MR MISSING — n_missing >= 1."""
        long_df = _make_comp_long(longitudinal)
        rows = completeness_rows(long_df)
        l002 = [r for r in rows if r["patient"] == "L002"]
        assert l002, "L002 not found in completeness_rows output"
        assert l002[0]["n_missing"] >= 1

    def test_pct_complete_in_valid_range(self, longitudinal):
        long_df = _make_comp_long(longitudinal)
        rows = completeness_rows(long_df)
        for row in rows:
            assert 0.0 <= row["pct_complete"] <= 100.0


# --------------------------------------------------------------------------- #
# render_cohort_report — end-to-end render test (Task 2)
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def rendered_html(cohort, longitudinal, tmp_path_factory):
    """Render the cohort report ONCE for all render assertions (plotly embed is slow).

    Returns (html_text: str, out_path: str).
    """
    from DICOM_discovery.report_cohort import render_cohort_report  # noqa: E402
    _rt_df, idx, _truth = cohort
    _root, long_idx = longitudinal
    # One index for both engines, as a real run has. Before the integrity and completeness
    # tabs were merged the fixture could feed them two disjoint cohorts and nothing noticed;
    # now that one row carries both verdicts, feeding them different patients would mean the
    # follow-up column was never rendered at all and the merge went untested.
    both = pd.concat([idx.table, long_idx.table], ignore_index=True)
    rt_study_df = build_rt_integrity(both)
    rollup_df = build_rt_rollup(both)
    comp_state, comp_hover, comp_long = build_completeness(long_idx.table, DEFAULT_PROTOCOL)
    manifest = long_idx.manifest
    out_dir = tmp_path_factory.mktemp("render_output")
    out_path = out_dir / "cohort_report.html"
    result = render_cohort_report(
        rt_study_df, rollup_df, comp_state, comp_hover, comp_long,
        manifest, DEFAULT_PROTOCOL, str(out_path), table=both
    )
    html_text = out_path.read_text(encoding="utf-8")
    return html_text, result


class TestRenderCohortReport:
    """End-to-end render tests: assert structure of the single rendered HTML.

    All tests share the module-scoped ``rendered_html`` fixture so plotly.js is
    embedded only once (the embedding step takes several seconds).
    """

    def test_render_returns_path(self, rendered_html):
        """render_cohort_report must return a string path to the written file."""
        _html, result = rendered_html
        assert isinstance(result, str)
        assert result.endswith(".html")

    def test_render_self_contained_no_cdn(self, rendered_html):
        """No external CDN asset references at the HTML tag level — file must open air-gapped.

        Note: the embedded plotly.js bundle contains URL string *literals* (e.g. Mapbox/topojson
        defaults) that appear in the JS source. We check for HTML-level references only:
        <script src=…>, <link href=…>, and <img src=…> pointing to external origins.
        """
        html_text, _ = rendered_html
        # The canonical plotly CDN check (mirrors test_completeness.py)
        assert 'src="https://cdn.plot.ly' not in html_text, "CDN plotly script src found"
        # No external stylesheet link tags
        assert '<link rel="stylesheet" href="http' not in html_text, "External CSS <link> found"
        # No external <script src="http…"> tags (as opposed to JS string literals inside the bundle)
        assert '<script src="http' not in html_text, "External <script src> found"
        # No external <link href="http…"> tags (catches non-stylesheet link rels too)
        assert '<link href="http' not in html_text, "External <link href> found"
        # No @import of external stylesheets
        assert '@import url(http' not in html_text, "External @import url() found"

    def test_render_plotly_embedded_inline(self, rendered_html):
        """Plotly.js must be embedded inline — file size must exceed 1 MB."""
        html_text, _ = rendered_html
        assert len(html_text) > 1_000_000, (
            f"HTML too small ({len(html_text)} bytes) — Plotly.js probably not embedded inline"
        )

    def test_completeness_has_no_tab_of_its_own(self, rendered_html):
        """Integrity and follow-up are two verdicts about one patient, so they share one tab.

        Splitting them made a reader reconcile two tables by patient id to answer one
        question. Fails if a separate completeness tab or panel comes back.
        """
        html_text, _ = rendered_html
        assert 'data-tab="rt"' in html_text, "integrity tab not found in HTML"
        assert 'data-tab="map"' in html_text, "cohort map tab not found in HTML"
        assert 'data-tab="comp"' not in html_text, "completeness tab is back"
        assert 'data-panel="comp"' not in html_text, "completeness panel is back"
        assert "Integrity &amp; follow-up" in html_text, "merged tab label missing"

    def test_render_kpi_cards_present(self, rendered_html):
        """KPI section must contain OK/WARN/INCOMPLETE/NO_RT verdict labels + the completeness
        strip, labelled distinctly from the Completeness tab's all-or-nothing count so the two
        adjacent-looking numbers are not read as the same statistic."""
        html_text, _ = rendered_html
        assert 'class="kpis"' in html_text, "KPI section missing"
        for verdict in ("OK", "WARN", "INCOMPLETE", "NO_RT"):
            assert verdict in html_text, f"Verdict '{verdict}' not found in KPI section"
        assert "mean per-patient completeness" in html_text, (
            "'mean per-patient completeness' KPI label missing"
        )
        assert "cohort complete" not in html_text, (
            "stale 'cohort complete' label found - it must not read as the same statistic "
            "as the Completeness tab's 'patients complete' count"
        )

    def test_render_topbar_ruo_disclaimer(self, rendered_html):
        """Topbar must contain the RUO disclaimer text."""
        from DICOM_discovery.report_cohort import RUO_TEXT  # noqa: E402
        html_text, _ = rendered_html
        assert RUO_TEXT in html_text or "Research Use Only" in html_text, (
            "RUO disclaimer missing from topbar"
        )
        assert 'class="topbar"' in html_text, "Topbar element missing"

    def test_ruo_notice_is_byte_identical_in_report_and_standalone_page(self, rendered_html):
        """The regulatory notice must read the same everywhere it ships.

        report_cohort.py used to define its own RUO_TEXT with an em dash while
        report_completeness.py used an ASCII hyphen - both shipped, so the cohort report and
        the standalone `dicom-discovery completeness` page stated the notice differently. Would
        fail again if either module reintroduced its own copy of the string instead of
        importing the one definition.
        """
        from DICOM_discovery.completeness import DEFAULT_PROTOCOL
        from DICOM_discovery.report_cohort import RUO_TEXT as COHORT_RUO_TEXT
        from DICOM_discovery.report_completeness import (
            RUO_TEXT as COMP_RUO_TEXT,
        )
        from DICOM_discovery.report_completeness import (
            completeness_page_html,
        )

        assert COHORT_RUO_TEXT == COMP_RUO_TEXT == "Research Use Only - not a medical device."
        assert chr(0x2014) not in COHORT_RUO_TEXT, "em dash crept back into the regulatory notice"

        cohort_html, _ = rendered_html
        standalone_html = completeness_page_html([], {"n_patients": 0, "n_complete": 0,
                                                       "n_incomplete": 0, "worst_gap": None,
                                                       "n_unmapped": 0}, DEFAULT_PROTOCOL, [])
        assert COMP_RUO_TEXT in cohort_html
        assert COMP_RUO_TEXT in standalone_html

    def test_render_topbar_manifest_fields(self, rendered_html):
        """Topbar must surface the manifest summary: root, files, patients, studies, timestamp."""
        html_text, _ = rendered_html
        for key_label in ("root", "files seen", "patients", "studies", "generated"):
            assert key_label in html_text, f"Manifest label '{key_label}' missing from topbar"

    def test_render_rt_table_has_patient_rows(self, rendered_html):
        """RT integrity panel must contain the grid table and at least one patient row."""
        html_text, _ = rendered_html
        assert 'id="rt-table"' in html_text, "RT grid table missing"
        assert "class='prow" in html_text or 'class="prow' in html_text, (
            "No patient rows in RT table"
        )

    def test_render_drill_down_per_study_findings(self, rendered_html):
        """At least one drill-down panel (per-study findings) must be present in the RT table."""
        html_text, _ = rendered_html
        assert "class='drow'" in html_text or 'class="drow"' in html_text, (
            "No drill-down rows (drow) in RT table — per-study findings not rendered"
        )
        assert "Per-study findings" in html_text, "Drill-down title 'Per-study findings' not found"

    def test_render_no_google_fonts(self, rendered_html):
        """No Google Fonts link — only system or inlined fonts permitted."""
        html_text, _ = rendered_html
        assert "fonts.googleapis.com" not in html_text, (
            "Google Fonts reference found — violates air-gap rule"
        )
        assert "fonts.gstatic.com" not in html_text, "Google Fonts static CDN found"


class TestRegistreReskin:
    """v0.7 sober 'registre QC' re-skin: actionable column, non-colour-only
    verdict badge, and WARN/INCOMPLETE ordered to the top. The interactive Plotly
    completeness map must remain (covered by TestRenderCohortReport)."""

    def test_action_column_header_present(self, rendered_html):
        html_text, _ = rendered_html
        assert ">Action<" in html_text, "RT table is missing an Action column header"

    def test_recommended_actions_are_rendered(self, rendered_html):
        # P006 is INCOMPLETE (missing RTDOSE) -> re-export it; P007 misses PTV -> contour it.
        html_text, _ = rendered_html
        assert "Re-export RTDOSE" in html_text, "re-export action not surfaced in the report"
        assert "target contour" in html_text.lower(), "contouring action not surfaced in the report"

    def test_verdict_badge_is_not_colour_only(self, rendered_html):
        # WCAG: a verdict must be legible without colour — a per-status shape class,
        # a textual symbol, and an aria-label.
        html_text, _ = rendered_html
        assert "pill-INCOMPLETE" in html_text, "verdict badge lacks a per-status shape class"
        assert "aria-label=\"verdict: " in html_text or "aria-label='verdict: " in html_text, (
            "verdict badge lacks an accessible label"
        )

    def test_actionable_patients_are_ordered_first(self, rendered_html):
        import re
        html_text, _ = rendered_html
        statuses = re.findall(r"class='prow[^']*'[^>]*data-status='([A-Z_]+)'", html_text)
        assert statuses, "no patient rows parsed"
        actionable = {"WARN", "INCOMPLETE"}
        first_settled = next((i for i, s in enumerate(statuses) if s not in actionable), len(statuses))
        assert all(s in actionable for s in statuses[:first_settled]), (
            f"a settled verdict appears above an actionable one: {statuses}"
        )
        assert statuses[0] in actionable


class TestCohortMap:
    """The interactive cohort timeline map (reintegrated from file_discovery):
    a 'Cohort map' tab, points legended by modality, each point sourced on hover."""

    def test_map_tab_present(self, rendered_html):
        html_text, _ = rendered_html
        assert 'data-tab="map"' in html_text, "Cohort map tab missing"

    def test_map_is_plotly_and_air_gapped(self, rendered_html):
        html_text, _ = rendered_html
        assert "plotly" in html_text.lower(), "cohort map is not a plotly figure"
        assert 'src="https://cdn.plot.ly' not in html_text, "map pulls plotly from CDN (not air-gapped)"

    def test_each_point_is_sourced_on_hover(self, rendered_html):
        # The hover must expose the DICOM source path(s) — provenance per data point.
        # (Plotly unicode-escapes < and > inside its embedded JSON, so check the words.)
        html_text, _ = rendered_html
        assert "Source" in html_text, "hover does not expose a Source section"
        assert ".dcm" in html_text, "no DICOM source path embedded in the map hover data"

    def test_points_are_legended_by_modality(self, rendered_html):
        # Modalities appear as legend entries (trace names) in the embedded figure JSON.
        html_text, _ = rendered_html
        assert '"CT"' in html_text and '"MR"' in html_text, "modality legend entries missing"


class TestFollowUpMergedIntoTheIntegrityTable:
    """Completeness no longer has a tab; it rides in the integrity row, from the same
    renderer the standalone page uses."""

    def test_the_integrity_row_carries_the_follow_up_chips(self, rendered_html):
        """One row, both verdicts. Fails if the follow-up column is dropped or left empty."""
        html_text, _ = rendered_html
        assert "class='c-fu'" in html_text, "follow-up column missing from the integrity table"
        assert "class='fu-grp'" in html_text, "follow-up chips are not grouped by timepoint"
        for tp in DEFAULT_PROTOCOL.timepoints:
            assert f"class='fu-lab'>{tp}</span>" in html_text, (
                f"timepoint {tp} is not labelled in the follow-up cell — a chip that says "
                f"'MR missing' without saying which visit is half an instruction"
            )

    def test_the_cohort_gap_table_leads_the_tab(self, rendered_html):
        """The one thing on this tab that is not per-patient keeps its own block, above the
        queue: a QC round re-exports a timepoint and a modality, not a patient."""
        html_text, _ = rendered_html
        assert "id='comp-gaps'" in html_text, "cohort gap table missing"
        assert html_text.index("id='comp-gaps'") < html_text.index('id="rt-table"'), (
            "the gap table must come before the per-patient queue"
        )

    def test_no_patient_graded_by_the_protocol_is_silently_dropped(self, rendered_html):
        """The merged table is keyed on the RT rollup. Anyone the rollup does not list would
        simply not be drawn, and a patient silently absent from a QC registry is the one
        failure a QC registry cannot have."""
        from DICOM_discovery.report_cohort import followup_only_patients
        html_text, _ = rendered_html
        rows = [{"patient_id": "A"}]
        assert followup_only_patients(rows, {"A": {}, "B": {}}) == ["B"]
        assert followup_only_patients(rows, {"A": {}}) == []
        # In this fixture both engines see one index, so nothing is orphaned and no note fires.
        assert "have no row below" not in html_text

    def test_the_drill_down_gives_each_fact_its_own_card(self, rendered_html):
        """Six facts flowed together in one wrapping strip read as one paragraph of bold
        labels. Each gets a bordered card, and the left edge says what kind of fact it is:
        neutral for description, red for the problem, green for the instruction."""
        html_text, _ = rendered_html
        assert "class='detail-meta issue'" in html_text, "the problem card is not marked"
        assert "class='detail-meta act'" in html_text, "the instruction card is not marked"
        assert "class='detail-meta wide'" in html_text, (
            "the wide facts (RT chain, follow-up) must span the row rather than be squeezed"
        )
        css = html_text.split("<style>", 1)[1].split("</style>", 1)[0]
        meta = css.split(".detail-meta{")[1].split("}")[0]
        assert "border:1px solid" in meta and "border-left:3px" in meta
        assert "max-width:34ch" not in meta, "the card sets the measure, not a ch cap"

    def test_the_displaced_study_counts_survive_in_the_drill_down(self, rendered_html):
        """Studies and RT studies left the table to make room. They did not leave the report:
        dropping a column has to mean moving the number, not losing it."""
        html_text, _ = rendered_html
        assert "<b>Studies</b>" in html_text
        assert "carry RT objects" in html_text

    def test_the_follow_up_cell_exports_its_meaning_not_its_glyphs(self, rendered_html):
        """A generic CSV export reads cell text, and a cell of chips reads as a run of glyphs
        glued to screen-reader words. The cell carries what it means instead."""
        import re
        html_text, _ = rendered_html
        exports = re.findall(r"class='c-fu' data-export=\"([^\"]*)\"", html_text)
        assert exports, "no follow-up cell carries a data-export value"
        assert any("missing:" in e for e in exports), (
            "no exported follow-up names the missing timepoint/modality pairs"
        )
        assert not any(("✓" in e or "✗" in e) for e in exports), (
            "a glyph reached the exported text"
        )

    def test_every_follow_up_chip_is_labelled_with_its_own_state(self, rendered_html):
        """Every chip, not merely one of them, must name its own state accessibly.

        An "any chip has a label" check passes when a single chip anywhere in the report keeps
        its aria-label. This counts the chips, requires the count of accessible labels to match
        exactly, and checks each label against that chip's own data attributes — so one
        unlabelled or mislabelled state branch fails.
        """
        import re
        html_text, _ = rendered_html
        chips = re.findall(r"<span class='chip chip-[A-Z]+'[^>]*>", html_text)
        assert chips, "no follow-up chips rendered in the report"
        assert len(chips) == html_text.count("class='chip "), "a chip is missing its state class"
        words = {"PRESENT": "present", "MISSING": "missing",
                 "EXTRA": "extra", "UNMAPPED": "unmapped"}
        for chip in chips:
            tp = re.search(r"data-tp='([^']*)'", chip).group(1)
            state = re.search(r"data-state='([^']*)'", chip).group(1)
            mod_m = re.search(r"data-mod='([^']*)'", chip)
            expected = (f"aria-label='{tp} {mod_m.group(1)} {words[state]}'" if mod_m
                        else f"aria-label='{tp} {words[state]}'")
            assert expected in chip, f"chip labelled inconsistently with its own state: {chip}"

    def test_no_follow_up_cell_carries_a_state_outside_the_four_valid_ones(self, rendered_html):
        """Only PRESENT / MISSING / EXTRA / UNMAPPED may reach the page.

        Stronger than pinning the old ``chip-NA`` class name: a grey "not expected" cell
        reintroduced under any other name would pass that check and fails this one, because
        the requirement is that a fifth state does not exist, not that one spelling is gone.
        """
        import re
        html_text, _ = rendered_html
        states = set(re.findall(r"data-state='([^']*)'", html_text))
        assert states, "no follow-up cell states rendered"
        assert states <= {"PRESENT", "MISSING", "EXTRA", "UNMAPPED"}, f"unexpected: {states}"

    def test_report_stylesheet_places_the_completeness_rules_after_the_base_rules(self, rendered_html):
        """Source order decides two overrides that tie on specificity — assert it on the page.

        `.cgrid{overflow:visible}` must follow `.grid{overflow:hidden}` (sticky header) and
        `.sw-blank{border-style:dashed}` must follow `.sw{border:...}` (blank swatch).
        Swapping the two style() calls in render_cohort_report reinstates both bugs.
        """
        html_text, _ = rendered_html
        css = html_text.split("<style>", 1)[1].split("</style>", 1)[0]
        assert css.index(".cgrid{") > css.index(".grid{")
        assert css.index(".sw-blank{") > css.index(".sw{")
        # The chip palette must also land after the base rules it tints.
        assert css.index(".chip-MISSING{") > css.index(".chip{")


class TestKpiFilterChips:
    """The cohort-size and verdict KPI segments are clickable filter chips that narrow
    both the RT table and the cohort map. The JS wiring keys off data-status."""

    def test_kpi_segments_are_buttons(self, rendered_html):
        html_text, _ = rendered_html
        assert "class='kpi kpi-btn'" in html_text, "KPI segments are not rendered as filter buttons"

    def test_all_filter_statuses_present(self, rendered_html):
        html_text, _ = rendered_html
        for status in ("ALL", "OK", "WARN", "INCOMPLETE", "NO_RT", "REVIEW"):
            assert f"data-status='{status}'" in html_text, f"KPI chip for '{status}' missing"

    def test_patients_chip_is_default_pressed(self, rendered_html):
        # The "patients" (ALL) chip starts pressed so the unfiltered view is the default.
        html_text, _ = rendered_html
        assert "data-status='ALL' aria-pressed='true'" in html_text, (
            "the 'patients' chip should be the default-pressed (ALL) filter"
        )

    def test_filter_wires_table_and_map(self, rendered_html):
        # The script must filter both the table (applyRtFilter) and the map (filterMap).
        html_text, _ = rendered_html
        assert "applyRtFilter" in html_text, "RT table filter function missing"
        assert "filterMap" in html_text, "cohort-map filter function missing"
