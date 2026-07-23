"""Fetch a bounded, open-source DICOM-RT cohort from TCIA for the real-data test tiers.

Dev tooling only (not part of the package import path). Standard library only — no new
dependency. Uses the public, unauthenticated NBIA REST API to download a small subset of a
public TCIA collection, so the package can be exercised end-to-end on **real** RT data with
**no PHI** (TCIA public collections are de-identified) and no committed DICOM.

Default collection: **Vestibular-Schwannoma-SEG** — the one public collection carrying a
complete MR->RTSTRUCT->RTPLAN->RTDOSE chain (brain radiosurgery; MR-based planning).

    Data usage (CC BY 4.0 — attribution required):
      * Dataset: Shapey, J., et al. (2021). Segmentation of Vestibular Schwannoma from MRI,
        An Open Annotated Dataset (Vestibular-Schwannoma-SEG). The Cancer Imaging Archive.
      * TCIA:    Clark, K., et al. (2013). The Cancer Imaging Archive (TCIA): Maintaining and
        Operating a Public Information Repository. J Digit Imaging 26(6), 1045-1057.

Examples:
    python bench/fetch_public_cohort.py --patient-id VS-SEG-001 --rt-only --out-dir examples/real_chain
    python bench/fetch_public_cohort.py --patients 3 --out-dir examples/real_cohort
    python bench/fetch_public_cohort.py --out-dir examples/real_chain --verify
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

NBIA_BASE = "https://services.cancerimagingarchive.net/nbia-api/services/v1"
DEFAULT_COLLECTION = "Vestibular-Schwannoma-SEG"
RT_MODALITIES = ("RTSTRUCT", "RTPLAN", "RTDOSE")
PROVENANCE_NAME = "provenance.json"

ATTRIBUTION = (
    "Data: Vestibular-Schwannoma-SEG (TCIA), CC BY 4.0. Cite Shapey et al. 2021 (dataset) "
    "and Clark et al. 2013 (TCIA). Public, de-identified - no PHI."
)


class FetchError(RuntimeError):
    """Raised when the TCIA API is unreachable/degraded after retries (callers may skip)."""


def _urlopen_retry(url: str, timeout: float, retries: int = 3, backoff: float = 1.0) -> bytes:
    """GET a URL with an explicit socket timeout, small retry/backoff, and a broad error catch.

    A degraded-but-reachable endpoint must not hang: the socket timeout bounds each attempt and
    any network-level failure (connection, HTTP, or timeout) is retried then surfaced as a
    FetchError so a test tier can skip rather than fail.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "DICOM_discovery-fetch"})
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, TimeoutError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise FetchError(f"TCIA request failed after {retries} attempts: {url}\n  last error: {last}")


def _get_json(path: str, timeout: float) -> list:
    raw = _urlopen_retry(NBIA_BASE + path, timeout=timeout)
    return json.loads(raw.decode("utf-8"))


def list_series(collection: str, patient_id: Optional[str], timeout: float) -> List[dict]:
    """Return the series metadata for a collection (optionally one patient)."""
    q = {"Collection": collection}
    if patient_id:
        q["PatientID"] = patient_id
    return _get_json("/getSeries?" + urllib.parse.urlencode(q), timeout=timeout)


def _download_series(series_uid: str, dest: Path, timeout: float) -> List[Path]:
    """Download one series (a ZIP from /getImage) and extract its files into ``dest``.

    Filenames inside the per-series ZIP are not unique across series (each starts at
    ``00000001.dcm``), so a short, stable per-series tag is prefixed to avoid collisions when
    several series of the same modality land in one directory.
    """
    url = NBIA_BASE + "/getImage?" + urllib.parse.urlencode({"SeriesInstanceUID": series_uid})
    blob = _urlopen_retry(url, timeout=timeout)
    tag = hashlib.sha1(series_uid.encode()).hexdigest()[:8]
    written: List[Path] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            target = dest / f"{tag}_{Path(name).name}"
            target.write_bytes(zf.read(name))
            written.append(target)
    return written


def fetch_cohort(collection: str, out_dir: str, *, patient_id: Optional[str] = None,
                 n_patients: Optional[int] = None, modalities: Optional[List[str]] = None,
                 rt_only: bool = False, timeout: float = 60.0) -> dict:
    """Fetch a bounded subset into ``out_dir/<PatientID>/<Modality>/`` and write provenance.json.

    Returns a manifest dict (counts + provenance path). Prints counts + attribution only.
    """
    wanted = set(RT_MODALITIES) if rt_only else (set(modalities) if modalities else None)
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    series = list_series(collection, patient_id, timeout)
    if not patient_id and n_patients:
        keep = sorted({s.get("PatientID", "") for s in series})[:n_patients]
        series = [s for s in series if s.get("PatientID") in keep]
    if wanted:
        series = [s for s in series if s.get("Modality") in wanted]
    if not series:
        raise FetchError(f"no matching series for collection={collection} patient={patient_id}")

    files: List[Path] = []
    for s in series:
        pid, mod = s.get("PatientID", "unknown"), s.get("Modality", "NA")
        dest = root / pid / mod
        dest.mkdir(parents=True, exist_ok=True)
        files.extend(_download_series(s["SeriesInstanceUID"], dest, timeout))

    prov = write_provenance(collection, series, root)
    n_pat = len({s.get("PatientID") for s in series})
    print(f"Fetched {len(files)} files, {len(series)} series, {n_pat} patient(s) -> {root}")
    print(ATTRIBUTION)
    return {"n_files": len(files), "n_series": len(series), "n_patients": n_pat,
            "provenance": str(prov)}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def write_provenance(collection: str, series: List[dict], root: Path) -> Path:
    """Pin the fetched content: collection, series UIDs, and per-file SHA-256."""
    file_hashes: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != PROVENANCE_NAME:
            file_hashes[str(p.relative_to(root)).replace("\\", "/")] = _sha256(p)
    prov = {
        "collection": collection,
        "license": "CC BY 4.0",
        "citation": ["Shapey et al. 2021 (dataset)", "Clark et al. 2013 (TCIA)"],
        "series_instance_uids": sorted(s["SeriesInstanceUID"] for s in series),
        "patient_ids": sorted({s.get("PatientID", "") for s in series}),
        "file_sha256": file_hashes,
    }
    out = root / PROVENANCE_NAME
    out.write_text(json.dumps(prov, indent=2, sort_keys=True), encoding="utf-8")
    return out


def verify(out_dir: str) -> bool:
    """Re-check every file against provenance.json. Returns True iff all hashes match."""
    root = Path(out_dir)
    prov_path = root / PROVENANCE_NAME
    if not prov_path.exists():
        print(f"no {PROVENANCE_NAME} in {root}")
        return False
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    ok = True
    for rel, expected in prov.get("file_sha256", {}).items():
        p = root / rel
        if not p.exists() or _sha256(p) != expected:
            print(f"MISMATCH/MISSING: {rel}")
            ok = False
    print("verify: OK" if ok else "verify: FAILED")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch a bounded open-source DICOM-RT cohort from TCIA.")
    ap.add_argument("--collection", default=DEFAULT_COLLECTION)
    ap.add_argument("--patient-id", default=None, help="fetch exactly this patient (e.g. VS-SEG-001)")
    ap.add_argument("--patients", type=int, default=None, help="fetch the first N patients")
    ap.add_argument("--modalities", default=None, help="comma-separated modality allowlist")
    ap.add_argument("--rt-only", action="store_true", help="only RTSTRUCT/RTPLAN/RTDOSE (a small chain)")
    ap.add_argument("--out-dir", default="examples/real_chain")
    ap.add_argument("--timeout", type=float, default=60.0, help="per-request socket timeout (s)")
    ap.add_argument("--verify", action="store_true", help="re-check out-dir against provenance.json, then exit")
    args = ap.parse_args(argv)

    if args.verify:
        return 0 if verify(args.out_dir) else 1
    try:
        fetch_cohort(args.collection, args.out_dir, patient_id=args.patient_id,
                     n_patients=args.patients,
                     modalities=(args.modalities.split(",") if args.modalities else None),
                     rt_only=args.rt_only, timeout=args.timeout)
    except FetchError as e:
        print(f"fetch unavailable: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
