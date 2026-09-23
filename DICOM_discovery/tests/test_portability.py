"""Portability: what someone needs on an unknown machine — a version to quote in a bug
report, an environment check that explains *why* a run will fail, and CSVs that open
correctly in a French Excel (real ROI names and paths carry accents: "Moelle epiniere",
"D:/Donnees/...")."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import __version__  # noqa: E402
from DICOM_discovery.cli import main  # noqa: E402

BOM = b"\xef\xbb\xbf"


# --------------------------------------------------------------------------- #
# --version
# --------------------------------------------------------------------------- #
def test_version_flag_prints_the_tool_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def test_doctor_reports_the_environment_a_bug_report_needs(capsys):
    rc = main(["doctor"])
    out = capsys.readouterr().out

    assert rc == 0
    assert __version__ in out
    for expected in ("Python", "platform", "pydicom", "pandas", "plotly", "stdout encoding"):
        assert expected in out, expected


def test_doctor_reports_whether_the_folder_picker_works(capsys):
    """The standalone binary's double-click flow needs tkinter; doctor must say whether this
    machine (or this frozen build) actually has it."""
    main(["doctor"])
    assert "folder picker" in capsys.readouterr().out


def test_doctor_notes_when_the_folder_picker_is_unavailable(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "tkinter", None)  # a trimmed Python without tkinter

    main(["doctor"])

    out = capsys.readouterr().out
    assert "folder picker" in out and "typed" in out


def test_doctor_fails_when_the_dicom_root_is_unreadable(tmp_path, capsys):
    rc = main(["doctor", "--root", str(tmp_path / "not_mounted")])
    assert rc == 1
    assert "not readable" in capsys.readouterr().out


def test_doctor_fails_when_the_output_folder_cannot_be_written(tmp_path, capsys):
    blocker = tmp_path / "out"
    blocker.write_text("a file where the output folder should be", encoding="utf-8")

    rc = main(["doctor", "--output-dir", str(blocker)])

    assert rc == 1
    assert "not writable" in capsys.readouterr().out


def test_doctor_passes_on_a_usable_pair_of_folders(tmp_path, capsys):
    (tmp_path / "data").mkdir()
    rc = main(["doctor", "--root", str(tmp_path / "data"), "--output-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "readable" in out and "writable" in out
    # doctor may create the output folder, but must leave no probe file behind.
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == []


# --------------------------------------------------------------------------- #
# Excel-readable CSV (accented French reasons), BOM-free JSON
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def rt_check_outputs(cohort, tmp_path_factory):
    """Run `rt-check` once, writing both CSVs and the verdict JSON."""
    out = tmp_path_factory.mktemp("csv_out")
    _df, idx, _truth = cohort
    csv = out / "rt.csv"
    rc = main(["rt-check", "--root", str(Path(idx.manifest["root"])),
               "--out-csv", str(csv), "--json", str(out / "verdicts.json")])
    return rc, csv, out / "rt_by_patient.csv", out / "verdicts.json"


def test_csv_opens_in_a_french_excel(rt_check_outputs):
    """Excel on a French Windows reads a BOM-less UTF-8 CSV as cp1252: accented ROI names
    and paths from a real cohort ("Moelle épinière") turn into mojibake."""
    rc, per_study, per_patient, _ = rt_check_outputs
    assert rc == 0
    for path in (per_study, per_patient):
        assert path.read_bytes().startswith(BOM), path.name


def test_csv_still_round_trips_through_pandas(rt_check_outputs):
    _rc, per_study, _pp, _json = rt_check_outputs
    df = pd.read_csv(per_study)
    assert "patient_id" in df.columns  # no stray BOM glued to the first column name


def test_verdict_json_has_no_bom(rt_check_outputs):
    """A BOM would break strict JSON parsers consuming the verdict contract."""
    _rc, _ps, _pp, payload = rt_check_outputs
    raw = payload.read_bytes()
    assert not raw.startswith(BOM)
    assert json.loads(raw.decode("utf-8"))["tool"] == "DICOM_discovery"

