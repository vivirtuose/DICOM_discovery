"""Unattended, scheduled cohort run — the command a hospital NAS executes (``dicom-discovery job``).

Designed for DSM Task Scheduler / cron / a systemd timer / a container restart policy, where
nobody watches the console:

* **One folder per run** — ``<output>/runs/<UTC stamp>/`` holds the report, the verdict JSON,
  the CSVs, the index manifest and ``run.log``. Failed runs keep their log for diagnosis.
* **``<output>/latest/``** mirrors the last *usable* run, file by file with atomic replaces,
  so the bookmark clinicians open is never half-written and never replaced by a failure.
* **``<output>/last_run.json``** — status for monitoring. Counts only, no patient identifiers.
* **Lock** — ``<output>/.dicom-discovery.lock`` stops a long scan and the next scheduled one
  from overlapping; a lock older than ``--stale-lock-hours`` (a crashed run) is taken over.
* **Retention** — only the newest ``--keep`` run folders are kept; nothing else is touched.
* **Index cache on by default** (``<output>/.cache/index_cache.pkl``) so nightly re-scans of a
  ~1M-file share only read new/changed files.
* The scanned tree is only ever read: a read-only mount is the recommended deployment.

Exit codes: 0 success · 1 no DICOM (nothing published) · 2 unexpected error ·
3 partial scan (published, but some directories could not be listed) · 75 another run holds
the lock (EX_TEMPFAIL — "try again later").
"""
from __future__ import annotations

import contextlib
import datetime
import json
import logging
import os
import re
import shutil
import socket
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

from . import __version__
from .completeness import DEFAULT_PROTOCOL, build_completeness, load_protocol
from .contract import build_verdict_payload, validate_payload
from .fsutil import atomic_write_bytes, atomic_write_text
from .indexer import build_index
from .report_cohort import render_cohort_report, verdict_counts
from .rt_integrity import build_rt_integrity, build_rt_rollup

LOG = logging.getLogger("DICOM_discovery.job")

EXIT_OK, EXIT_NO_DICOM, EXIT_ERROR, EXIT_PARTIAL, EXIT_LOCKED = 0, 1, 2, 3, 75
LOCK_NAME = ".dicom-discovery.lock"
STATUS_NAME = "last_run.json"
RUN_STAMP_RE = re.compile(r"^\d{8}T\d{6}Z(-\d+)?$")


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(ts: datetime.datetime) -> str:
    return ts.isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Lock
# --------------------------------------------------------------------------- #
def _acquire_lock(lock: Path, stale_hours: float) -> bool:
    """Create the lock file exclusively. Take over a lock older than ``stale_hours``."""
    info = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "started_utc": _iso(_utc_now())})
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age_h = (time.time() - lock.stat().st_mtime) / 3600
            except FileNotFoundError:
                continue  # released between our attempt and the stat: retry once
            if age_h < stale_hours:
                return False
            LOG.warning("taking over a stale lock (%.1f h old, previous run crashed?): %s", age_h, lock)
            with contextlib.suppress(FileNotFoundError):
                lock.unlink()
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(info)
        return True
    return False


# --------------------------------------------------------------------------- #
# Run folder, latest mirror, retention
# --------------------------------------------------------------------------- #
def _new_run_dir(runs: Path, started: datetime.datetime) -> Path:
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    for i in range(1000):
        cand = runs / (stamp if i == 0 else f"{stamp}-{i}")
        try:
            cand.mkdir(parents=True)
            return cand
        except FileExistsError:
            continue
    raise RuntimeError(f"cannot allocate a run folder under {runs}")


def _publish_latest(run_dir: Path, latest: Path) -> None:
    """Mirror the run into ``latest/`` — each file replaced atomically, never half-written."""
    latest.mkdir(parents=True, exist_ok=True)
    for f in sorted(run_dir.iterdir()):
        if f.is_file():
            atomic_write_bytes(latest / f.name, f.read_bytes())


def _prune_runs(runs: Path, keep: int) -> None:
    """Delete all but the newest ``keep`` run folders; only folders named like a run stamp."""
    if keep <= 0:
        return
    stamped = sorted(p for p in runs.iterdir() if p.is_dir() and RUN_STAMP_RE.match(p.name))
    for old in stamped[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
        LOG.info("pruned old run %s", old.name)


# --------------------------------------------------------------------------- #
# Logging: everything printed or logged during the run also lands in run.log
# --------------------------------------------------------------------------- #
class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            s.write(data)
        return len(data)

    def flush(self):
        for s in self._streams:
            s.flush()


@contextlib.contextmanager
def _run_log(path: Path):
    fh = open(path, "a", encoding="utf-8")
    handler = logging.StreamHandler(fh)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    pkg_logger = logging.getLogger("DICOM_discovery")
    prev_level = pkg_logger.level
    if pkg_logger.getEffectiveLevel() > logging.INFO:
        pkg_logger.setLevel(logging.INFO)
    pkg_logger.addHandler(handler)
    try:
        with contextlib.redirect_stdout(_Tee(sys.stdout, fh)):
            yield fh
    finally:
        pkg_logger.removeHandler(handler)
        pkg_logger.setLevel(prev_level)
        fh.close()


# --------------------------------------------------------------------------- #
# The job
# --------------------------------------------------------------------------- #
def _write_status(out: Path, status: dict) -> None:
    atomic_write_text(out / STATUS_NAME, json.dumps(status, indent=2, ensure_ascii=False))


def _counts(manifest: dict) -> dict:
    keys = ("n_files_seen", "n_dicom_indexed", "n_unreadable", "n_dirs_excluded",
            "n_dirs_unreadable", "n_patients", "n_studies")
    return {k: manifest.get(k, 0) for k in keys}


def run_job(args, preflight) -> int:
    """Run one scheduled cohort pass. ``preflight`` is the CLI's preflight printer."""
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    lock = out / LOCK_NAME
    if not _acquire_lock(lock, args.stale_lock_hours):
        print(f"another dicom-discovery job holds {lock} — exiting (code {EXIT_LOCKED}).",
              file=sys.stderr)
        return EXIT_LOCKED

    started = _utc_now()
    run_dir: Optional[Path] = None
    status = {"tool_version": __version__, "root": str(args.root), "started_utc": _iso(started)}
    code = EXIT_ERROR
    try:
        run_dir = _new_run_dir(out / "runs", started)
        status["run_dir"] = run_dir.relative_to(out).as_posix()
        with _run_log(run_dir / "run.log"):
            code = _run(args, out, run_dir, status, preflight)
        if code in (EXIT_OK, EXIT_PARTIAL):  # after run.log is closed, so it is mirrored whole
            _publish_latest(run_dir, out / "latest")
            _prune_runs(out / "runs", args.keep)
    except Exception as exc:  # noqa: BLE001 — a scheduler needs a status, not a stack dump
        status.update(status="error", error=f"{type(exc).__name__}: {exc}")
        if run_dir is not None:
            with open(run_dir / "run.log", "a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc())
        print(f"job failed: {exc}", file=sys.stderr)
        code = EXIT_ERROR
    finally:
        finished = _utc_now()
        status.update(exit_code=code, finished_utc=_iso(finished),
                      duration_s=round((finished - started).total_seconds(), 1))
        try:
            _write_status(out, status)
        finally:
            with contextlib.suppress(FileNotFoundError):
                lock.unlink()
    return code


def _run(args, out: Path, run_dir: Path, status: dict, preflight) -> int:
    protocol = load_protocol(args.protocol) if args.protocol else DEFAULT_PROTOCOL
    cache = None if args.no_cache else (args.cache or str(out / ".cache" / "index_cache.pkl"))
    print(f"dicom-discovery {__version__} job — root={args.root} run={run_dir.name}")

    idx = build_index(args.root, patient_regexes=args.patient_regex, group_by=args.group_by,
                      progress=False, workers=args.workers, cache=cache,
                      assume_immutable=args.assume_immutable, exclude_dirs=args.exclude_dir)
    status.update(_counts(idx.manifest))
    if not preflight(idx, protocol):
        status["status"] = "no_dicom"
        return EXIT_NO_DICOM

    rt_study_df = build_rt_integrity(idx.table)
    rollup_df = build_rt_rollup(idx.table)
    comp_state, comp_hover, comp_long = build_completeness(idx.table, protocol)

    atomic_write_text(run_dir / "index_manifest.json", json.dumps(idx.manifest, indent=2))
    atomic_write_text(run_dir / "rt_integrity.csv", rt_study_df.to_csv(index=False))
    atomic_write_text(run_dir / "rt_integrity_by_patient.csv", rollup_df.to_csv(index=False))
    payload = build_verdict_payload(rollup_df, manifest=idx.manifest, protocol_name=protocol.name)
    validate_payload(payload)
    atomic_write_text(run_dir / "verdicts.json", json.dumps(payload, indent=2, ensure_ascii=False))
    render_cohort_report(rt_study_df, rollup_df, comp_state, comp_hover, comp_long, idx.manifest,
                         protocol, str(run_dir / "cohort_report.html"), table=idx.table)

    partial = bool(idx.manifest.get("n_dirs_unreadable"))
    status.update(status="partial" if partial else "success", verdicts=verdict_counts(rollup_df))
    print(f"run {'PARTIAL' if partial else 'OK'} -> {run_dir}")
    return EXIT_PARTIAL if partial else EXIT_OK
