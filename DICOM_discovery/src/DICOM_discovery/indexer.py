"""DICOM indexer — the single point that turns any file tree into a canonical table.

This is the boundary that makes the package adaptive: nothing downstream ever looks at
the filesystem. The path stops being *data* and becomes an opaque pointer used only to
re-open the bytes. Patient / study / series / modality identity is read from the DICOM
**headers** (PS3.3 model), not parsed from folder positions.

Design decisions (from the generalization council):
* DICOM files are detected by **content** (the ``DICM`` preamble or a parseable
  SOPClassUID), never by a ``*.dcm`` extension — real PACS exports are often extensionless.
* Patient key resolution is a traced fallback chain: ``PatientID`` tag → ``--patient-regex``
  on the path → top-level folder. Each row records which strategy was used.
* One row per series for images (CT slices collapse to one), one row per RT object.
* Unreadable DICOM-looking files are *counted and reported*, never silently dropped.
"""
from __future__ import annotations

import collections
import datetime
import fnmatch
import logging
import os
import pickle
import re
import sys
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Pattern, Sequence, Tuple, TypeVar

import pandas as pd

from .fsutil import atomic_write_bytes

try:
    from pydicom import dcmread

    _HAS_PYDICOM = True
except Exception:  # pragma: no cover
    _HAS_PYDICOM = False

try:
    from tqdm import tqdm

    _HAS_TQDM = True
except Exception:  # pragma: no cover
    _HAS_TQDM = False

LOG = logging.getLogger("DICOM_discovery.indexer")

# SOP Class UID -> short modality label.
SOP_CLASS: Dict[str, str] = {
    "1.2.840.10008.5.1.4.1.1.481.3": "RTSTRUCT",
    "1.2.840.10008.5.1.4.1.1.481.2": "RTDOSE",
    "1.2.840.10008.5.1.4.1.1.481.5": "RTPLAN",
    "1.2.840.10008.5.1.4.1.1.2": "CT",
    "1.2.840.10008.5.1.4.1.1.4": "MR",
    "1.2.840.10008.5.1.4.1.1.128": "PT",
    "1.2.840.10008.5.1.4.1.1.20": "NM",
    "1.2.840.10008.5.1.4.1.1.66.4": "SEG",
}
DICOMDIR_SOP = "1.2.840.10008.1.3.10"  # Media Storage Directory — not an image/RT object.

# Extensions that are never DICOM: skip instantly so we never force-parse, e.g., a zip.
NON_DICOM_EXTS = {
    ".zip", ".gz", ".tar", ".7z", ".rar", ".bz2", ".xz",
    ".txt", ".json", ".csv", ".tsv", ".xlsx", ".xls", ".pdf", ".log", ".md",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif",
    ".nii", ".mgz", ".nrrd", ".mha", ".mhd", ".py", ".html", ".xml", ".yaml", ".yml",
}
# A preamble-less file larger than this is not force-parsed as DICOM (avoids big archives).
MAX_FORCE_BYTES = 50 * 1024 * 1024

# Only these tags are parsed from each header (the canonical record + the sequences whose
# contents we read). Restricting parsing cuts bytes/CPU on slow shares. CRITICAL: every
# field populated by ``read_instance`` must be reachable from this set — see
# tests/test_indexer.py::test_canonical_table_fully_populated_nested_fields.
SPECIFIC_TAGS = [
    "SOPClassUID", "Modality", "PatientID", "StudyInstanceUID", "StudyDate",
    "SeriesInstanceUID", "SOPInstanceUID", "FrameOfReferenceUID",
    "ReferencedFrameOfReferenceSequence", "StructureSetROISequence",
    "ReferencedStructureSetSequence", "FractionGroupSequence",
    "DoseUnits", "DoseSummationType", "ReferencedRTPlanSequence",
]

# First intermediate cache flush after this many files read; the interval then doubles, so a
# ~1M-file scan flushes ~8 times (linear total I/O) instead of rewriting the cache every 5000.
DEFAULT_CHECKPOINT_EVERY = 5000

# Directories that NAS appliances, snapshots and SMB/AFP clients create inside shares. They
# hold deleted data (recycle bins), full duplicate copies (snapshots) or thumbnails — never
# live cohort data — so they are pruned from the walk (matched case-insensitively by name).
NAS_SYSTEM_DIRS = frozenset(d.casefold() for d in (
    "@eaDir", "#recycle", "#snapshot", "@sharebin", "@tmp",                     # Synology
    "@Recycle", "@Recently-Snapshot", ".@__thumb", "@__thumb", ".@__qini",     # QNAP
    ".snapshot", ".snapshots", "~snapshot", ".zfs",                             # NetApp / NFS / ZFS
    "$RECYCLE.BIN", "System Volume Information",                                # Windows clients
    ".AppleDouble", ".AppleDB", ".AppleDesktop", ".TemporaryItems", ".Trashes",
    ".Spotlight-V100", ".fseventsd",                                            # macOS clients
    "lost+found",
))
NAS_SYSTEM_DIR_PREFIXES = (".trash-",)  # freedesktop per-user trash (.Trash-1000)
# Client litter files: never DICOM, and a ``._x.dcm`` AppleDouble must not count as unreadable.
LITTER_FILES = frozenset(f.casefold() for f in (".DS_Store", "Thumbs.db", "desktop.ini", "ehthumbs.db"))
# Unreadable directory paths kept verbatim in the manifest (the count is always exact).
MAX_LISTED_UNREADABLE_DIRS = 50
# Header reads in flight at once, per worker thread (bounded streaming — see _bounded_map).
READS_IN_FLIGHT_PER_WORKER = 32

TABLE_COLUMNS = [
    "path", "source_root", "patient_id", "patient_id_source",
    "study_uid", "study_date", "series_uid", "modality",
    "sop_class_uid", "sop_instance_uid", "frame_of_reference",
    "roi_names", "ref_struct_uids", "ref_plan_uids",
    "dose_units", "dose_sum_type", "n_fractions",
]


@dataclass
class IndexResult:
    table: pd.DataFrame
    unreadable: List[Dict[str, str]] = field(default_factory=list)
    manifest: Dict = field(default_factory=dict)


def _has_dicm_preamble(path: str) -> bool:
    """Standalone preamble check (kept for direct callers/tests). The hot path in
    ``read_instance`` sniffs the preamble from the already-open handle instead, so a
    candidate file is opened at most once per scan."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(132)
    except Exception:
        return False
    return len(head) >= 132 and head[128:132] == b"DICM"


def _seq(ds, name: str) -> list:
    return list(getattr(ds, name, []) or [])


def _shared(ds, name: str) -> str:
    """Header value as a str, interned: every slice of a series carries the same study /
    series / frame-of-reference UIDs, so ~1M in-memory records share one copy of each
    (~1.3 GB -> ~0.9 GB per million files, measured) instead of one copy per slice."""
    return sys.intern(str(getattr(ds, name, "") or "").strip())


def read_instance(path: str) -> Tuple[Optional[dict], bool]:
    """Read one DICOM header into a canonical record.

    Returns ``(record_or_None, looked_like_dicom)``: ``record`` is ``None`` when the file
    is not DICOM / unreadable, and ``looked_like_dicom`` is True when it bore the ``DICM``
    preamble (so the caller can flag an unreadable-but-DICOM-looking file without re-opening).

    Content-based: a file is accepted only if it has the ``DICM`` preamble or yields a
    ``SOPClassUID`` once parsed (``force=True``). This catches preamble-less DICOM while
    rejecting junk. DICOMDIR objects are skipped (they are directories, not instances).

    I/O: the file is opened **once**. We read the 132-byte preamble, ``seek(0)``, and hand
    the same open handle to ``dcmread`` — no second open, and no separate ``os.stat`` for
    the size guard (taken from ``os.fstat`` on the open handle).
    """
    if not _HAS_PYDICOM:
        raise RuntimeError("pydicom is required to read DICOM headers")
    ext = os.path.splitext(path)[1].lower()
    if ext in NON_DICOM_EXTS:
        return None, False  # known non-DICOM: never open/force-parse (e.g. multi-MB archives)
    # For a .dcm/.ima file, skip the preamble sniff (slow-share win) but still require a
    # SOPClassUID below — a garbage .dcm is rejected, not trusted blindly.
    trusted_ext = ext in (".dcm", ".ima")
    try:
        fh = open(path, "rb")
    except Exception as exc:  # noqa: BLE001
        LOG.debug("unreadable (open): %s (%s)", path, exc)
        return None, False
    try:
        had_preamble = False
        if not trusted_ext:
            head = fh.read(132)
            had_preamble = len(head) >= 132 and head[128:132] == b"DICM"
            if not had_preamble:
                # Only force-parse small, preamble-less files (rare extensionless DICOM);
                # never parse a large archive that slipped past the extension check. Size
                # comes from the open handle (no extra stat round-trip).
                try:
                    if os.fstat(fh.fileno()).st_size > MAX_FORCE_BYTES:
                        return None, False
                except OSError:
                    return None, False
        fh.seek(0)
        try:
            ds = dcmread(fh, stop_before_pixels=True, force=True, specific_tags=SPECIFIC_TAGS)
        except Exception as exc:  # noqa: BLE001 - capture any read failure
            LOG.debug("unreadable: %s (%s)", path, exc)
            return None, had_preamble
    finally:
        fh.close()

    sop_class = _shared(ds, "SOPClassUID")
    if not sop_class and not had_preamble:
        return None, had_preamble  # no real preamble and no SOP class => not DICOM (or corrupt)
    if sop_class == DICOMDIR_SOP:
        return None, had_preamble  # index object, not an instance

    modality = SOP_CLASS.get(sop_class) or str(getattr(ds, "Modality", "") or "").upper()

    # FrameOfReferenceUID: direct (CT/MR/RTDOSE) or via sequence (RTSTRUCT/RTPLAN).
    for_uid = _shared(ds, "FrameOfReferenceUID")
    if not for_uid:
        rfors = _seq(ds, "ReferencedFrameOfReferenceSequence")
        if rfors:
            for_uid = _shared(rfors[0], "FrameOfReferenceUID")

    rec = {
        "path": str(path),
        "source_root": "",
        "patient_id": _shared(ds, "PatientID"),
        "patient_id_source": "dicom",
        "study_uid": _shared(ds, "StudyInstanceUID"),
        "study_date": _shared(ds, "StudyDate"),
        "series_uid": _shared(ds, "SeriesInstanceUID"),
        "modality": sys.intern(modality or "UNKNOWN"),
        "sop_class_uid": sop_class,
        "sop_instance_uid": str(getattr(ds, "SOPInstanceUID", "") or ""),
        "frame_of_reference": for_uid,
        "roi_names": [],
        "ref_struct_uids": [],
        "ref_plan_uids": [],
        "dose_units": "",
        "dose_sum_type": "",
        "n_fractions": None,
    }

    if modality == "RTSTRUCT":
        rec["roi_names"] = [str(getattr(r, "ROIName", "") or "") for r in _seq(ds, "StructureSetROISequence")]
    elif modality == "RTPLAN":
        rec["ref_struct_uids"] = [
            str(getattr(r, "ReferencedSOPInstanceUID", "") or "")
            for r in _seq(ds, "ReferencedStructureSetSequence")
            if getattr(r, "ReferencedSOPInstanceUID", "")
        ]
        fgs = _seq(ds, "FractionGroupSequence")
        if fgs:
            nf = getattr(fgs[0], "NumberOfFractionsPlanned", None)
            rec["n_fractions"] = int(nf) if nf not in (None, "") else None
    elif modality == "RTDOSE":
        rec["dose_units"] = str(getattr(ds, "DoseUnits", "") or "")
        rec["dose_sum_type"] = str(getattr(ds, "DoseSummationType", "") or "")
        rec["ref_plan_uids"] = [
            str(getattr(r, "ReferencedSOPInstanceUID", "") or "")
            for r in _seq(ds, "ReferencedRTPlanSequence")
            if getattr(r, "ReferencedSOPInstanceUID", "")
        ]
    return rec, had_preamble


def _folder_key(path: str, root: Path, patterns: List[Pattern]) -> str:
    rel = Path(path).relative_to(root).parts
    return rel[0] if len(rel) >= 2 else ""


def _resolve_patient(rec: dict, root: Path, patterns: List[Pattern]) -> tuple:
    """Traced fallback: PatientID tag -> path regex -> top folder."""
    pid = rec["patient_id"]
    if pid:
        return pid, "dicom"
    for pat in patterns:
        m = pat.search(rec["path"])
        if m:
            return m.group(0).strip(), "regex"
    folder = _folder_key(rec["path"], root, patterns)
    if folder:
        return folder, "folder"
    return "", "none"


def _index_one(fp: str) -> Tuple[Optional[dict], bool]:
    """Read one file. Returns (record_or_None, looked_like_dicom_but_unreadable).

    Opens the file at most once: ``read_instance`` propagates whether the ``DICM`` preamble
    was seen, so the unreadable path never re-opens to re-sniff. A trusted ``.dcm/.ima``
    extension that failed to parse still counts as "looked like DICOM" (unreadable), matching
    the prior behaviour, without touching the disk again.
    """
    rec, had_preamble = read_instance(fp)
    if rec is not None:
        return rec, False
    if os.path.splitext(fp)[1].lower() in NON_DICOM_EXTS:
        return None, False
    looked = fp.lower().endswith((".dcm", ".ima")) or had_preamble
    return None, looked


def _is_excluded_dir(name: str, extra_patterns: Sequence[str]) -> bool:
    folded = name.casefold()
    if folded in NAS_SYSTEM_DIRS or folded.startswith(NAS_SYSTEM_DIR_PREFIXES):
        return True
    return any(fnmatch.fnmatch(folded, p.casefold()) for p in extra_patterns)


def _is_litter_file(name: str) -> bool:
    return name.startswith("._") or name.casefold() in LITTER_FILES


@dataclass
class _WalkStats:
    n_dirs_excluded: int = 0
    unreadable_dirs: List[str] = field(default_factory=list)


def _iter_files(root_path: Path, stats: Optional[_WalkStats] = None,
                exclude_dirs: Sequence[str] = ()) -> Iterator[str]:
    """Stream candidate file paths from the tree (os.walk over os.scandir) without
    materializing the full ~1M-path list first — reads can begin immediately.

    NAS system / recycle / snapshot folders (and ``exclude_dirs`` name patterns) are pruned,
    client litter files skipped, and every directory that cannot be listed (permissions,
    unmounted share) is recorded in ``stats`` — never silently dropped, as ``os.walk`` does
    by default."""
    stats = stats if stats is not None else _WalkStats()

    def _onerror(err: OSError) -> None:
        stats.unreadable_dirs.append(str(err.filename) if err.filename else str(root_path))
        LOG.warning("cannot list directory %s: %s", err.filename, err.strerror or err)

    for dp, dirnames, fns in os.walk(root_path, onerror=_onerror):
        kept = [d for d in dirnames if not _is_excluded_dir(d, exclude_dirs)]
        stats.n_dirs_excluded += len(dirnames) - len(kept)
        dirnames[:] = kept  # prune in place: os.walk will not descend into excluded dirs
        for fn in fns:
            if not _is_litter_file(fn):
                yield os.path.join(dp, fn)


_T = TypeVar("_T")
_R = TypeVar("_R")


def _bounded_map(ex: Executor, fn: Callable[[_T], _R], items: Iterable[_T], window: int) -> Iterator[_R]:
    """Ordered parallel map that keeps at most ``window`` calls in flight.

    ``Executor.map`` submits the *whole* iterable before yielding the first result — on a
    ~1M-file share that is ~2 GB of pending futures (measured), enough to exhaust a NAS.
    Pulling paths only as results are consumed keeps memory flat whatever the tree size.
    """
    pending: collections.deque = collections.deque()
    for item in items:
        pending.append(ex.submit(fn, item))
        if len(pending) >= window:
            yield pending.popleft().result()
    while pending:
        yield pending.popleft().result()


def build_index(root: str, patient_regexes: Optional[List[str]] = None,
                group_by: str = "dicom", progress: bool = False,
                workers: int = 8, cache: Optional[str] = None,
                assume_immutable: bool = False,
                checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
                exclude_dirs: Optional[Sequence[str]] = None) -> IndexResult:
    """Walk ``root`` and build the canonical instance table.

    ``group_by='dicom'`` keys patients by the ``PatientID`` tag (with traced fallback);
    ``group_by='folder'`` forces the top-level folder as the patient key (v1-compatible).
    Header reads are parallelised (I/O-bound over network shares); ``workers`` threads.
    Paths are streamed into the executor (no full list materialized first).

    ``cache`` (opt-in): a path where raw header records are persisted keyed by
    (path, mtime, size). Unchanged files are reused on the next run instead of re-read —
    turning a multi-hour re-scan into seconds. Patient keying is always re-applied, so
    changing ``--group-by`` between runs stays correct. The cache is flushed to disk (atomically)
    after ``checkpoint_every`` files read, then at doubling intervals, and once at the end — so
    a long/crashed scan resumes, while total cache I/O stays linear in the tree size.

    NAS system folders (recycle bins, snapshots, thumbnail stores — see ``NAS_SYSTEM_DIRS``)
    and any ``exclude_dirs`` name patterns (fnmatch, case-insensitive) are not walked; their
    count is recorded as ``n_dirs_excluded``. Directories that cannot be listed are recorded
    as ``n_dirs_unreadable`` / ``unreadable_dirs`` so a partial scan is never mistaken for a
    complete one.

    ``assume_immutable`` (opt-in, **for immutable / append-only archives only**): when set
    together with an existing ``cache``, the per-file ``os.stat`` is skipped and the cache is
    keyed by path alone. Files not in the cache are still read (new files appear); files that
    were *modified* in place are intentionally **not** re-read. Do not use on a tree whose
    files change content without being added/removed.
    """
    root_path = Path(root)
    patterns = []
    for p in (patient_regexes or []):
        try:
            patterns.append(re.compile(p, re.IGNORECASE))
        except re.error as exc:
            LOG.warning("ignoring invalid --patient-regex %r: %s", p, exc)

    cached_raw: Dict[str, dict] = {}
    cached_meta: Dict[str, tuple] = {}
    if cache and os.path.exists(cache):
        try:
            blob = pickle.loads(Path(cache).read_bytes())
            cached_raw, cached_meta = blob.get("records", {}), blob.get("meta", {})
            LOG.info("loaded index cache (%d entries) from %s", len(cached_meta), cache)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("ignoring unreadable cache %s: %s", cache, exc)

    raw_by_path: Dict[str, dict] = {}
    file_meta: Dict[str, tuple] = {}
    unreadable: List[Dict[str, str]] = []
    all_paths: List[str] = []   # every path seen this scan, in walk order (output order)

    def _checkpoint() -> None:
        if not cache:
            return
        try:
            atomic_write_bytes(cache, pickle.dumps({"records": raw_by_path, "meta": file_meta}))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("could not write cache %s: %s", cache, exc)

    # Decide, per streamed path, whether it must be read. Reuse from cache where possible.
    def _plan(fp: str) -> bool:
        """Record the path (output order) and return True if it must be (re)read."""
        all_paths.append(fp)
        if not cache:
            return True
        if assume_immutable:
            # Key by path alone: no os.stat. Reuse any cached record/negative; read only
            # files absent from the cache (new files). Modified files are NOT re-read.
            if fp in cached_meta or fp in cached_raw:
                file_meta[fp] = cached_meta.get(fp, ("", 0))
                if fp in cached_raw:
                    raw_by_path[fp] = cached_raw[fp]
                return False
            return True
        try:
            st = os.stat(fp)
        except OSError:
            all_paths.pop()  # vanished between walk and stat — not part of this scan
            return False
        meta = (st.st_mtime, st.st_size)
        file_meta[fp] = meta
        if cached_meta.get(fp) == meta:
            if fp in cached_raw:           # cached positive
                raw_by_path[fp] = cached_raw[fp]
            # else: cached negative (non-DICOM) — skip without reading
            return False
        return True

    def _read_pair(fp: str) -> Tuple[str, Tuple[Optional[dict], bool]]:
        return fp, _index_one(fp)

    # Stream paths into the executor: ``_plan`` records every path (output order) and the
    # cache reuse as it goes, yielding only the paths that still need a header read. The
    # worker returns ``(path, result)`` so the path survives the parallel map.
    walk = _WalkStats()
    to_read = (fp for fp in _iter_files(root_path, walk, exclude_dirs or ()) if _plan(fp))

    n_read = 0
    next_checkpoint = max(1, checkpoint_every) if checkpoint_every else 0
    n_workers = max(1, workers)
    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        results = _bounded_map(ex, _read_pair, to_read, window=n_workers * READS_IN_FLIGHT_PER_WORKER)
        if progress and _HAS_TQDM:
            results = tqdm(results, unit="file", desc="indexing")
        for fp, (rec, looked) in results:
            if rec is None and looked:
                unreadable.append({"path": fp, "reason": "unreadable_or_non_dicom"})
            if rec is not None:
                raw_by_path[fp] = rec
            n_read += 1
            if cache and next_checkpoint and n_read >= next_checkpoint:
                _checkpoint()
                next_checkpoint = n_read * 2  # geometric: O(log n) flushes, linear total I/O

    n_files = len(all_paths)
    LOG.info("walked %s: %d files seen, %d read (%d threads)", root_path, n_files, n_read, workers)
    if cache:
        _checkpoint()

    # Resolve the patient key, then collapse image series to one row (CT/MR slices) while
    # keeping every RT object. Deduplicating *before* copying records keeps peak memory at
    # the size of the collapsed table, not of the ~1M per-slice records.
    no_series: List[dict] = []
    by_series: List[dict] = []
    seen_series = set()
    for fp in all_paths:
        raw = raw_by_path.get(fp)
        if raw is None:
            continue
        if group_by == "folder":
            pid, pid_source = _folder_key(fp, root_path, patterns), "folder"
        else:
            pid, pid_source = _resolve_patient(raw, root_path, patterns)
        series_uid = str(raw["series_uid"])
        if series_uid:
            if (pid, series_uid) in seen_series:
                continue
            seen_series.add((pid, series_uid))
        rec = dict(raw)
        rec["source_root"] = str(root_path)
        rec["patient_id"], rec["patient_id_source"] = pid, pid_source
        (by_series if series_uid else no_series).append(rec)

    df = pd.DataFrame(no_series + by_series, columns=TABLE_COLUMNS)

    manifest = {
        "tool": "DICOM_discovery",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "root": str(root_path),
        "group_by": group_by,
        "patient_regexes": list(patient_regexes or []),
        "n_files_seen": n_files,
        "n_dicom_indexed": int(len(df)),
        "n_unreadable": len(unreadable),
        "n_dirs_excluded": walk.n_dirs_excluded,
        "exclude_dirs": list(exclude_dirs or []),
        "n_dirs_unreadable": len(walk.unreadable_dirs),
        "unreadable_dirs": walk.unreadable_dirs[:MAX_LISTED_UNREADABLE_DIRS],
        "n_patients": int(df["patient_id"].nunique()) if not df.empty else 0,
        "n_studies": int(df["study_uid"].nunique()) if not df.empty else 0,
        "patient_id_source_counts": (df["patient_id_source"].value_counts().to_dict() if not df.empty else {}),
        "modalities": (df["modality"].value_counts().to_dict() if not df.empty else {}),
    }
    LOG.info("indexed %d DICOM instances from %d files (%d unreadable), %d patients / %d studies",
             manifest["n_dicom_indexed"], n_files, manifest["n_unreadable"],
             manifest["n_patients"], manifest["n_studies"])
    return IndexResult(table=df, unreadable=unreadable, manifest=manifest)
