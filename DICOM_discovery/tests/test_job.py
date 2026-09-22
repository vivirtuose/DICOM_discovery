"""``dicom-discovery job`` — the unattended, scheduled run used on a hospital NAS.

Contract (what a scheduler — DSM Task Scheduler, cron, systemd timer — relies on):
  * one timestamped folder per run under ``<output-dir>/runs/`` (report, verdicts, CSVs,
    manifest, run.log); ``<output-dir>/latest/`` always holds the last *usable* run;
  * ``<output-dir>/last_run.json`` — machine-readable status, counts only (no PHI);
  * a lock prevents two overlapping runs; a stale lock (crashed run) is taken over;
  * old runs are pruned (``--keep``), nothing else in the output folder is touched;
  * the scanned DICOM tree is never written to (read-only mounts work);
  * exit codes: 0 ok · 1 no DICOM · 2 error · 3 partial scan · 75 locked.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery.cli import main  # noqa: E402

RUN_FILES = {"cohort_report.html", "verdicts.json", "index_manifest.json",
             "rt_integrity.csv", "rt_integrity_by_patient.csv", "run.log"}
LOCK = ".dicom-discovery.lock"


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_mtime for p in root.rglob("*")}


def _runs(out: Path) -> list:
    return sorted(p.name for p in (out / "runs").iterdir() if p.is_dir() and p.name[0].isdigit())


def _status(out: Path) -> dict:
    return json.loads((out / "last_run.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def two_runs(longitudinal, tmp_path_factory):
    """Run the job twice on the same cohort: a cold run, then — over a stale lock left by a
    'crashed' run — a warm run with ``--keep 1``. Rendering is slow, so it is shared."""
    root, _ = longitudinal
    out = tmp_path_factory.mktemp("nas_output")
    before = _tree(Path(root))

    rc1 = main(["job", "--root", root, "--output-dir", str(out), "--workers", "2"])
    first = {"rc": rc1, "runs": _runs(out), "status": _status(out)}
    first["files"] = {p.name for p in (out / "runs" / first["runs"][0]).iterdir()} if first["runs"] else set()

    (out / "runs" / "notes").mkdir()                      # an operator's folder: never pruned
    lock = out / LOCK
    lock.write_text("{}", encoding="utf-8")
    old = time.time() - 3 * 24 * 3600
    os.utime(lock, (old, old))                            # left behind by a crash 3 days ago

    rc2 = main(["job", "--root", root, "--output-dir", str(out), "--workers", "2", "--keep", "1"])
    return {"root": Path(root), "out": out, "before": before, "first": first, "rc2": rc2}


def test_job_writes_a_complete_timestamped_run(two_runs):
    first = two_runs["first"]
    assert first["rc"] == 0
    assert len(first["runs"]) == 1
    assert RUN_FILES <= first["files"]


def test_job_status_file_is_machine_readable_and_phi_free(two_runs):
    st = _status(two_runs["out"])
    assert st["status"] == "success" and st["exit_code"] == 0
    assert st["n_patients"] > 0 and st["n_dicom_indexed"] > 0
    assert sum(st["verdicts"].values()) == st["n_patients"]
    assert st["run_dir"].startswith("runs/")
    assert "patients" not in st  # counts only — patient identifiers stay in the run folder


def test_latest_mirrors_the_last_successful_run(two_runs):
    out = two_runs["out"]
    newest = out / "runs" / _runs(out)[-1]
    assert RUN_FILES <= {p.name for p in (out / "latest").iterdir()}
    assert (out / "latest" / "verdicts.json").read_bytes() == (newest / "verdicts.json").read_bytes()


def test_job_never_writes_into_the_scanned_tree(two_runs):
    assert _tree(two_runs["root"]) == two_runs["before"]


def test_index_cache_defaults_to_the_output_folder_and_is_reused(two_runs):
    out = two_runs["out"]
    assert (out / ".cache" / "index_cache.pkl").exists()
    log = (out / "latest" / "run.log").read_text(encoding="utf-8")
    assert "loaded index cache" in log


def test_stale_lock_is_taken_over_and_released(two_runs):
    assert two_runs["rc2"] == 0
    assert not (two_runs["out"] / LOCK).exists()


def test_old_runs_are_pruned_but_foreign_folders_kept(two_runs):
    out = two_runs["out"]
    assert len(_runs(out)) == 1
    assert _runs(out) != two_runs["first"]["runs"]  # the newer run survived
    assert (out / "runs" / "notes").is_dir()


def test_job_refuses_to_overlap_a_running_job(longitudinal, tmp_path):
    root, _ = longitudinal
    (tmp_path / LOCK).write_text("{}", encoding="utf-8")   # fresh lock = a run in progress

    rc = main(["job", "--root", root, "--output-dir", str(tmp_path)])

    assert rc == 75
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "last_run.json").exists()
    assert (tmp_path / LOCK).exists()                      # the other run's lock is untouched


def test_no_dicom_fails_without_clobbering_latest(tmp_path):
    root = tmp_path / "empty_share"
    root.mkdir()
    out = tmp_path / "out"
    (out / "latest").mkdir(parents=True)
    (out / "latest" / "verdicts.json").write_text("previous", encoding="utf-8")

    rc = main(["job", "--root", str(root), "--output-dir", str(out)])

    assert rc == 1
    assert _status(out)["status"] == "no_dicom"
    assert (out / "latest" / "verdicts.json").read_text(encoding="utf-8") == "previous"
    assert (out / "runs" / _runs(out)[0] / "run.log").exists()  # diagnosable afterwards
    assert not (out / LOCK).exists()


def test_unexpected_error_is_recorded_and_lock_released(longitudinal, tmp_path, monkeypatch):
    import DICOM_discovery.job as job

    def broken(_table):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(job, "build_rt_integrity", broken)
    root, _ = longitudinal

    rc = main(["job", "--root", root, "--output-dir", str(tmp_path)])

    assert rc == 2
    st = _status(tmp_path)
    assert st["status"] == "error" and "simulated failure" in st["error"]
    log = (tmp_path / "runs" / _runs(tmp_path)[0] / "run.log").read_text(encoding="utf-8")
    assert "Traceback" in log
    assert not (tmp_path / LOCK).exists()


def test_partial_scan_publishes_but_exits_3(synthetic_rt_root, tmp_path, monkeypatch):
    """A directory the service account cannot list: the report is still produced (and says
    PARTIAL), but the scheduler sees a non-zero exit so someone is alerted."""
    locked = synthetic_rt_root / "P003"
    real_scandir = os.scandir

    def scandir(path="."):
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(locked)):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    rc = main(["job", "--root", str(synthetic_rt_root), "--output-dir", str(tmp_path), "--no-cache"])

    assert rc == 3
    st = _status(tmp_path)
    assert st["status"] == "partial" and st["n_dirs_unreadable"] == 1
    assert (tmp_path / "latest" / "cohort_report.html").exists()
    assert not (tmp_path / ".cache").exists()


@pytest.fixture()
def synthetic_rt_root(tmp_path_factory):
    from DICOM_discovery import generate_synthetic_cohort

    root = tmp_path_factory.mktemp("rt_share")
    generate_synthetic_cohort(str(root))
    return root
