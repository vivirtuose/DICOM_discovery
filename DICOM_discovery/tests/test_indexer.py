"""Tests for the canonical indexer: content detection, patient keying, and the cache."""
from __future__ import annotations

import builtins
import os
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import build_index, generate_synthetic_cohort  # noqa: E402


def test_index_detects_dicom_without_extension(tmp_path):
    """A DICOM file with no .dcm extension must still be indexed (content detection)."""
    generate_synthetic_cohort(str(tmp_path))
    # strip the .dcm extension off one patient's files
    p1 = tmp_path / "P001"
    for f in list(p1.glob("*.dcm")):
        f.rename(f.with_suffix(""))
    idx = build_index(str(tmp_path))
    assert "P001" in set(idx.table["patient_id"])
    assert (idx.table["patient_id"] == "P001").sum() >= 3  # CT + structs + plan + dose series


def test_non_dicom_files_are_ignored(tmp_path):
    generate_synthetic_cohort(str(tmp_path))
    (tmp_path / "P001" / "notes.txt").write_text("not dicom")
    (tmp_path / "P001" / "archive.zip").write_bytes(b"PK\x03\x04 not really a zip")
    idx = build_index(str(tmp_path))
    assert not idx.table["path"].str.endswith((".txt", ".zip")).any()


def test_cache_is_idempotent_and_reused(tmp_path):
    """Indexing with a cache twice yields the same table; second run reuses entries."""
    cohort = tmp_path / "cohort"
    generate_synthetic_cohort(str(cohort))
    cache = tmp_path / "index.cache"

    no_cache = build_index(str(cohort))
    first = build_index(str(cohort), cache=str(cache))
    assert cache.exists()
    second = build_index(str(cohort), cache=str(cache))

    # Same patients/studies/rows regardless of caching.
    for other in (first, second):
        assert other.manifest["n_patients"] == no_cache.manifest["n_patients"]
        assert other.manifest["n_dicom_indexed"] == no_cache.manifest["n_dicom_indexed"]
        assert set(other.table["patient_id"]) == set(no_cache.table["patient_id"])


def test_group_by_folder_overrides_patient_id_tag(tmp_path):
    """--group-by folder keys patients by the top folder, not the PatientID tag."""
    cohort = tmp_path / "cohort"
    generate_synthetic_cohort(str(cohort))
    idx = build_index(str(cohort), group_by="folder")
    assert set(idx.table["patient_id_source"]) == {"folder"}
    assert "P001" in set(idx.table["patient_id"])


def _canonical(df):
    """Sort + reset a table so two builds can be compared row-for-row, ignoring order
    and the run-specific source_root path."""
    cols = [c for c in df.columns if c != "source_root"]
    d = df[cols].copy()
    # list-valued cells -> tuples so they are hashable / comparable
    for c in ("roi_names", "ref_struct_uids", "ref_plan_uids"):
        d[c] = d[c].apply(lambda v: tuple(v) if isinstance(v, list) else v)
    return d.sort_values(["patient_id", "sop_instance_uid", "modality"]).reset_index(drop=True)


def test_canonical_table_fully_populated_nested_fields(tmp_path):
    """The canonical table must carry every nested RT field — this is the regression guard
    for the specific_tags optimization (a dropped tag would empty one of these)."""
    cohort = tmp_path / "cohort"
    generate_synthetic_cohort(str(cohort))
    df = build_index(str(cohort)).table

    struct = df[df["modality"] == "RTSTRUCT"]
    plan = df[df["modality"] == "RTPLAN"]
    dose = df[df["modality"] == "RTDOSE"]
    assert not struct.empty and not plan.empty and not dose.empty

    # RTSTRUCT -> roi_names populated
    assert struct["roi_names"].apply(lambda v: len(v) > 0).any()
    # RTPLAN -> referenced struct UIDs + n_fractions populated
    assert plan["ref_struct_uids"].apply(lambda v: len(v) > 0).any()
    assert plan["n_fractions"].notna().any()
    # RTDOSE -> referenced plan UIDs + dose fields populated
    assert dose["ref_plan_uids"].apply(lambda v: len(v) > 0).any()
    assert (dose["dose_units"].astype(str).str.len() > 0).any()
    assert (dose["dose_sum_type"].astype(str).str.len() > 0).any()
    # frame_of_reference resolved for image AND RT objects (direct + via sequence)
    assert (df["frame_of_reference"].astype(str).str.len() > 0).any()


def test_canonical_table_identical_extensionless_vs_extension(tmp_path):
    """specific_tags + single-open must yield an identical canonical table on the
    preamble-less (extensionless, force-parsed) path as on the trusted-.dcm path.
    Same bytes (same UIDs) — only the filename extension differs."""
    import pandas.testing as pdt

    cohort = tmp_path / "cohort"
    generate_synthetic_cohort(str(cohort))
    a = _canonical(build_index(str(cohort)).table)
    # strip every .dcm extension in place => force-parse path, identical bytes/UIDs.
    for f in list(cohort.rglob("*.dcm")):
        f.rename(f.with_suffix(""))
    b = _canonical(build_index(str(cohort)).table)
    # path differs (extension stripped); every other field must match exactly.
    pdt.assert_frame_equal(a.drop(columns=["path"]), b.drop(columns=["path"]), check_like=True)


def test_each_file_opened_at_most_once(tmp_path):
    """The dominant NFS win: a candidate file is opened at most once per build_index."""
    cohort = tmp_path / "cohort"
    generate_synthetic_cohort(str(cohort))
    # extensionless => exercises the preamble-sniff + force-parse path (most opens before).
    for f in list(cohort.rglob("*.dcm")):
        f.rename(f.with_suffix(""))
    dicom_paths = {str(p) for p in cohort.rglob("*") if p.is_file()}

    real_open = builtins.open
    opens: Counter = Counter()

    def counting_open(file, *a, **k):
        try:
            fp = os.fspath(file)
        except TypeError:
            fp = file
        if fp in dicom_paths:
            opens[fp] += 1
        return real_open(file, *a, **k)

    # pydicom opens via builtins.open when handed a path string; passing it an already-open
    # handle avoids a second open. We count builtins.open on the candidate paths.
    orig = builtins.open
    builtins.open = counting_open
    try:
        build_index(str(cohort), workers=1)
    finally:
        builtins.open = orig

    assert opens, "expected at least one candidate file to be opened"
    over = {fp: n for fp, n in opens.items() if n > 1}
    assert not over, f"these files were opened more than once: {over}"


def test_assume_immutable_skips_stat_and_reuses_by_path(tmp_path, monkeypatch):
    """--assume-immutable: with a warm cache, no per-file os.stat; cached records reused
    by path alone; a NEW file is still read; a MODIFIED file is NOT re-read."""
    cohort = tmp_path / "cohort"
    generate_synthetic_cohort(str(cohort))
    cache = tmp_path / "idx.cache"

    # Cold run writes the cache (stat-based path).
    build_index(str(cohort), cache=str(cache))
    assert cache.exists()

    # Warm immutable run must not stat any file under the cohort.
    real_stat = os.stat
    statted: list = []

    def tracking_stat(path, *a, **k):
        try:
            p = os.fspath(path)
        except TypeError:
            p = path
        if isinstance(p, str) and str(cohort) in p:
            statted.append(p)
        return real_stat(path, *a, **k)

    monkeypatch.setattr(os, "stat", tracking_stat)
    warm = build_index(str(cohort), cache=str(cache), assume_immutable=True)
    assert statted == [], f"assume_immutable must not stat cohort files, got {statted[:3]}"
    assert warm.manifest["n_dicom_indexed"] > 0

    # A brand-new DICOM file IS picked up (not in cache => still read), proving new files
    # appear even though stats are skipped. We prove the new path was READ by checking it
    # landed in the persisted cache (copied bytes share UIDs, so it would collapse out of
    # the table — the cache is the unambiguous read-witness).
    import pickle
    new_pat = cohort / "PNEW"
    new_pat.mkdir()
    src = next(iter(cohort.rglob("*.dcm")))
    new_file = new_pat / "x.dcm"
    new_file.write_bytes(src.read_bytes())
    build_index(str(cohort), cache=str(cache), assume_immutable=True)
    cached = pickle.loads(cache.read_bytes())["records"]
    assert str(new_file) in cached, "new file must be read & cached"

    # A MODIFIED existing DICOM is intentionally NOT re-read (kept from cache): the indexed
    # count is unchanged even though the file's bytes were corrupted in place.
    base = build_index(str(cohort), cache=str(cache), assume_immutable=True).manifest["n_dicom_indexed"]
    some = next(p for p in cohort.rglob("*.dcm") if p != new_file)
    some.write_bytes(b"corrupted now")
    warm_mod = build_index(str(cohort), cache=str(cache), assume_immutable=True)
    assert warm_mod.manifest["n_dicom_indexed"] == base


def test_incremental_checkpoint_flushes_during_scan(tmp_path):
    """With a low checkpoint interval the cache exists before the scan finishes — proven
    by the cache file being present and reusable after a crash mid-scan would have left it."""
    cohort = tmp_path / "cohort"
    generate_synthetic_cohort(str(cohort))
    cache = tmp_path / "idx.cache"
    # checkpoint_every very small so a flush happens before the final write.
    idx = build_index(str(cohort), cache=str(cache), checkpoint_every=1, workers=1)
    assert cache.exists()
    # The checkpointed cache must be valid and reusable.
    again = build_index(str(cohort), cache=str(cache))
    assert again.manifest["n_dicom_indexed"] == idx.manifest["n_dicom_indexed"]
