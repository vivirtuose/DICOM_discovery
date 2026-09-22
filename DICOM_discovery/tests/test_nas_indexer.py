"""Indexer hardening for hospital NAS shares (Synology / QNAP / NetApp / SMB / NFS).

A NAS share is not a clean export: it carries the appliance's own metadata and recycle
folders, macOS/Windows client litter, and directories the service account may not be
allowed to list. None of that may leak into — or silently vanish from — a cohort verdict.
"""
from __future__ import annotations

import os
import pickle
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import build_index, generate_synthetic_cohort  # noqa: E402
from DICOM_discovery.cli import main  # noqa: E402
from DICOM_discovery.fsutil import atomic_write_bytes  # noqa: E402


@pytest.fixture()
def cohort(tmp_path):
    root = tmp_path / "cohort"
    generate_synthetic_cohort(str(root))
    return root


def _copy_patient_into(root: Path, *parts: str) -> Path:
    """Duplicate patient P001 under ``root/<parts...>`` (e.g. a NAS recycle bin)."""
    dest = root.joinpath(*parts)
    shutil.copytree(root / "P001", dest)
    return dest


@pytest.mark.parametrize("system_dir", [
    ("#recycle",),                  # Synology recycle bin (deleted data!)
    ("@eaDir",),                    # Synology thumbnail / extended-attribute store
    ("#snapshot", "daily"),         # Synology visible snapshots (a full second copy)
    ("@Recycle",),                  # QNAP recycle bin
    (".snapshot", "hourly.0"),      # NetApp / generic NFS snapshots
    ("$RECYCLE.BIN",),              # Windows client recycle bin on an SMB share
])
def test_nas_system_directories_are_not_indexed(cohort, system_dir):
    """Deleted, snapshotted or thumbnailed copies must never count as cohort data."""
    # A patient that only survives in the recycle bin / a snapshot must not reappear...
    shutil.move(str(cohort / "P007"), str(cohort.joinpath(*system_dir, "P007")))
    # ...and a stale copy nested inside a live patient folder must not shadow the live files.
    _copy_patient_into(cohort, "P002", *system_dir)

    idx = build_index(str(cohort))

    assert "P007" not in set(idx.table["patient_id"])
    assert not any(system_dir[0] in p for p in idx.table["path"])
    assert idx.manifest["n_dirs_excluded"] == 2


def test_extra_exclude_dir_patterns(cohort):
    """Site-specific junk folders can be excluded by name pattern (``--exclude-dir``)."""
    _copy_patient_into(cohort, "_old_exports", "P001")
    idx = build_index(str(cohort), exclude_dirs=["_old*"])
    assert not any("_old_exports" in p for p in idx.table["path"])
    assert idx.manifest["n_dirs_excluded"] == 1


def test_client_litter_files_are_not_counted_unreadable(cohort):
    """macOS AppleDouble ``._x.dcm`` and OS thumbnail files are litter, not broken DICOM."""
    (cohort / "P001" / "._P001_CT_000.dcm").write_bytes(b"\x00\x05\x16\x07 AppleDouble")
    (cohort / "P001" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (cohort / "P001" / "Thumbs.db").write_bytes(b"\xd0\xcf\x11\xe0")

    idx = build_index(str(cohort))

    assert idx.manifest["n_unreadable"] == 0


def test_unlistable_directory_is_reported_not_silently_skipped(cohort, monkeypatch, capsys):
    """A directory the service account cannot list must surface in the manifest/preflight:
    a report built on a partially-visible tree must never look complete."""
    locked = cohort / "P003"
    real_scandir = os.scandir

    def scandir(path="."):
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(locked)):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    idx = build_index(str(cohort))

    assert idx.manifest["n_dirs_unreadable"] == 1
    assert idx.manifest["unreadable_dirs"] == [str(locked)]
    assert "P003" not in set(idx.table["patient_id"])

    main(["index", "--root", str(cohort), "--dry-run"])
    out = capsys.readouterr().out
    assert "unreadable dirs" in out and "1" in out
    assert "PARTIAL" in out


def test_missing_root_is_an_error_not_an_empty_cohort(tmp_path):
    """An unmounted share (root path absent) must be reported as unreadable, not as 0 files."""
    idx = build_index(str(tmp_path / "not_mounted"))
    assert idx.manifest["n_dirs_unreadable"] == 1


def test_atomic_write_keeps_previous_file_when_write_fails(tmp_path, monkeypatch):
    """A failed write (flaky share, full volume) leaves the old file intact and no temp file."""
    target = tmp_path / "index.cache"
    target.write_bytes(b"previous-good-content")

    def boom(_fd):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"new-content")

    assert target.read_bytes() == b"previous-good-content"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["index.cache"]


def test_atomic_write_replaces_content(tmp_path):
    target = tmp_path / "sub" / "out.json"
    atomic_write_bytes(target, b"v1")
    atomic_write_bytes(target, b"v2")
    assert target.read_bytes() == b"v2"
    assert sorted(p.name for p in target.parent.iterdir()) == ["out.json"]


def test_cache_checkpoints_are_amortized(cohort, tmp_path, monkeypatch):
    """Checkpointing must not rewrite the whole cache every N files (quadratic I/O on a NAS
    with ~1M files). With a tiny ``checkpoint_every`` the number of flushes stays logarithmic
    in the number of files read, and the final cache is complete."""
    import DICOM_discovery.indexer as indexer

    writes = []
    real = indexer.atomic_write_bytes

    def counting(path, data):
        writes.append(len(data))
        return real(path, data)

    monkeypatch.setattr(indexer, "atomic_write_bytes", counting)
    cache = tmp_path / "idx.cache"
    idx = build_index(str(cohort), cache=str(cache), checkpoint_every=1)

    n_files = idx.manifest["n_files_seen"]
    assert n_files > 20
    # 1 flush per file would be n_files writes; geometric growth keeps it ~log2(n) + final.
    assert len(writes) <= n_files.bit_length() + 2
    blob = pickle.loads(cache.read_bytes())
    assert len(blob["meta"]) == n_files


def test_parallel_reads_stream_with_a_bounded_window():
    """The path stream must be consumed lazily. ``Executor.map`` submits *every* path before
    yielding anything (~2 GB of pending futures for a 1M-file share); the bounded map keeps
    at most ``window`` reads in flight, so memory stays flat on a small-RAM NAS."""
    from concurrent.futures import ThreadPoolExecutor

    from DICOM_discovery.indexer import _bounded_map

    pulled = []

    def paths():
        for i in range(1000):
            pulled.append(i)
            yield i

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = _bounded_map(ex, lambda x: x * 2, paths(), window=16)
        first = next(results)
        assert first == 0
        assert len(pulled) <= 17
        rest = list(results)

    assert [first] + rest == [i * 2 for i in range(1000)]  # order preserved, nothing lost


def test_slices_of_one_series_share_their_uid_strings(cohort):
    """~1M slice records are held in memory during a scan (1.3 GB measured). Values common
    to a whole series must be one shared object, not one copy per slice (~0.9 GB)."""
    from DICOM_discovery.indexer import read_instance

    # Two independent reads stand in for two slices of the same series (same header values).
    a, _ = read_instance(str(cohort / "P001" / "ct.dcm"))
    b, _ = read_instance(str(cohort / "P001" / "ct.dcm"))
    for key in ("study_uid", "series_uid", "frame_of_reference", "patient_id", "sop_class_uid"):
        assert a[key] == b[key] and a[key] is b[key], key


def test_report_topbar_flags_a_partial_scan():
    """The HTML a clinician opens must itself say the scan was partial."""
    from DICOM_discovery.report_cohort import _topbar_html

    html = _topbar_html({"root": "/data", "n_dirs_unreadable": 2})
    assert "unreadable dirs" in html
    assert "unreadable dirs" not in _topbar_html({"root": "/data", "n_dirs_unreadable": 0})
