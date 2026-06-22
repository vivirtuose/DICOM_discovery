"""CLI integration tests for the ``dicom-discovery report`` subcommand (Task 3, Tier 2).

TDD: these tests were written BEFORE the implementation.  They exercise the public
contract — handler return code + file presence/absence — not internal details.

Contract:
  - ``report --root <dir> --out <path>`` writes an HTML file and returns 0.
  - ``report --root <dir> --dry-run``    writes NO file and returns 0.
  - ``report --root <empty> --out <path>`` writes NO file and returns 1 (preflight refuses
    empty scan).

Performance note: the full render embeds ~3.5 MB of Plotly JS and is slow.
All tests that assert the HTML was *written* share a single module-scoped fixture
(``report_result``) so render_cohort_report is called exactly once in this file.
The dry-run and empty-root tests never reach the render path — verified by contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery.cli import main  # noqa: E402

# ---------------------------------------------------------------------------
# Module-scoped fixture: run ``report`` once, reuse result across tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report_result(longitudinal, tmp_path_factory):
    """Run ``report`` once, return (exit_code, out_path, json_path).

    Plotly embed is slow — this fixture is module-scoped so the render happens
    exactly once across all tests that inspect the produced HTML. The same run
    also emits the versioned verdict JSON so the contract side is covered without
    a second render.
    """
    root, _idx = longitudinal
    out_dir = tmp_path_factory.mktemp("cli_report_output")
    out = out_dir / "r.html"
    js = out_dir / "verdicts.json"
    rc = main(["report", "--root", str(root), "--out", str(out), "--json", str(js)])
    return rc, out, js


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCmdReport:
    """CLI wiring tests for 'report'."""

    def test_report_returns_0(self, report_result):
        """``report --root <dir> --out <path>`` exits 0."""
        rc, _out, _js = report_result
        assert rc == 0, f"Expected exit 0, got {rc}"

    def test_report_writes_html(self, report_result):
        """``report --root <dir> --out <path>`` creates a non-empty HTML file."""
        _rc, out, _js = report_result
        assert out.exists(), "HTML output file was not created"
        assert out.stat().st_size > 0, "HTML output file is empty"

    def test_report_html_is_self_contained(self, report_result):
        """The produced HTML must embed Plotly inline (no CDN) — opens air-gapped."""
        _rc, out, _js = report_result
        html = out.read_text(encoding="utf-8")
        assert 'src="https://cdn.plot.ly' not in html, "CDN plotly src found — not self-contained"
        assert len(html) > 1_000_000, (
            f"HTML too small ({len(html)} bytes) — Plotly.js probably not embedded inline"
        )

    def test_report_dry_run_writes_nothing_and_returns_0(self, longitudinal, tmp_path):
        """``report --root <dir> --dry-run`` writes NO file and exits 0.

        Dry-run must not call render_cohort_report; nothing is written.
        This test is function-scoped (different tmp_path) so it can't share
        report_result without breaking the no-render guarantee.
        """
        root, _idx = longitudinal
        out = tmp_path / "dry_run_should_not_exist.html"
        rc = main(["report", "--root", str(root), "--out", str(out), "--dry-run"])
        assert rc == 0, f"Expected exit 0, got {rc}"
        assert not out.exists(), (
            "--dry-run must not write any file; preflight-only contract violated"
        )

    def test_report_empty_root_returns_1(self, tmp_path):
        """``report`` on an empty directory refuses to emit and exits 1."""
        empty = tmp_path / "nothing"
        empty.mkdir()
        out = tmp_path / "should_not_exist.html"
        rc = main(["report", "--root", str(empty), "--out", str(out)])
        assert rc == 1, f"Expected exit 1 on empty scan, got {rc}"
        assert not out.exists(), (
            "An empty scan must not produce an HTML output; "
            "preflight-refuses-empty contract violated"
        )


class TestCmdReportJson:
    """`report --json` emits the versioned, schema-valid verdict contract."""

    def test_json_file_is_written(self, report_result):
        _rc, _out, js = report_result
        assert js.exists() and js.stat().st_size > 0

    def test_json_is_a_valid_verdict_payload(self, report_result):
        import json

        from DICOM_discovery.contract import SCHEMA_VERSION, validate_payload

        _rc, _out, js = report_result
        payload = json.loads(js.read_text(encoding="utf-8"))
        validate_payload(payload)  # must not raise
        assert payload["schema_version"] == SCHEMA_VERSION
        assert isinstance(payload["patients"], list)

    def test_dry_run_writes_no_json(self, longitudinal, tmp_path):
        root, _idx = longitudinal
        out = tmp_path / "x.html"
        js = tmp_path / "x.json"
        rc = main(["report", "--root", str(root), "--out", str(out), "--json", str(js), "--dry-run"])
        assert rc == 0
        assert not js.exists()
