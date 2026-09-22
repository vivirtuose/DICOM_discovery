"""Double-click launcher: what happens when someone with no terminal habit runs the
standalone binary.

Double-clicking passes no arguments. Without a fallback, argparse would print "the following
arguments are required: command", exit 2, and the console window would vanish before anyone
could read it. So a frozen binary started with no arguments asks for the two folders it needs
and runs the scheduled job, then opens the report.

The folder picker and the browser are injected here, so the behaviour is tested without a
display server.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import cli  # noqa: E402
from DICOM_discovery.gui import run_interactive, should_launch_gui  # noqa: E402


# --------------------------------------------------------------------------- #
# When does the interactive mode take over?
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("argv", "frozen", "expected"), [
    ([], True, True),                       # double-clicked binary: no arguments
    ([], False, False),                     # bare `dicom-discovery` in a terminal: show help
    (["--gui"], False, True),               # explicit, so it can be tried without freezing
    (["job", "--root", "/d"], True, False),  # binary called from a scheduler: normal CLI
    (["--version"], True, False),
])
def test_interactive_mode_only_takes_over_when_it_should(argv, frozen, expected):
    assert should_launch_gui(argv, frozen=frozen) is expected


def test_cli_main_delegates_to_the_launcher(monkeypatch):
    """The wiring: a frozen binary with no arguments must not reach argparse."""
    called = []

    def fake_run_interactive():
        called.append(True)
        return 0

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr("DICOM_discovery.gui.run_interactive", fake_run_interactive)

    rc = cli.main([])

    assert rc == 0 and called == [True]


# --------------------------------------------------------------------------- #
# The interactive run itself
# --------------------------------------------------------------------------- #
def test_interactive_run_produces_a_report_and_opens_it(longitudinal, tmp_path, capsys):
    root, _ = longitudinal
    out = tmp_path / "qc"
    opened = []

    rc = run_interactive(
        ask_directory=lambda title: {"dicom": root, "output": str(out)}["dicom" if "DICOM" in title else "output"],
        open_file=opened.append,
        wait_for_user=lambda: None,
    )

    assert rc == 0
    report = out / "latest" / "cohort_report.html"
    assert report.is_file()
    assert opened == [str(report)]
    assert "Research Use Only" in capsys.readouterr().out


def test_cancelling_the_folder_picker_runs_nothing(tmp_path, capsys):
    rc = run_interactive(ask_directory=lambda title: "", open_file=lambda p: None,
                         wait_for_user=lambda: None)

    assert rc == 1
    assert "annul" in capsys.readouterr().out.lower()
    assert not list(tmp_path.iterdir())


def test_the_window_is_held_open_so_the_message_can_be_read(longitudinal, tmp_path):
    """A console that closes instantly is the classic double-click failure."""
    root, _ = longitudinal
    waited = []

    run_interactive(
        ask_directory=lambda title: root if "DICOM" in title else str(tmp_path / "qc"),
        open_file=lambda p: None,
        wait_for_user=lambda: waited.append(True),
    )

    assert waited == [True]
