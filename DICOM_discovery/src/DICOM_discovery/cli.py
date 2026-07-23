"""Command-line interface: ``dicom-discovery <command>`` (or ``python -m DICOM_discovery``).

Commands
--------
demo           generate synthetic cohorts, run both engines, write the completeness map.
index          build the canonical DICOM index of a tree (table CSV + manifest JSON).
rt-check       RT chain integrity QC on a directory (per patient/study).
completeness   observed-vs-expected completeness map for a longitudinal cohort.
report         unified RT-integrity + completeness cohort report (self-contained HTML).

``--dry-run`` (on index/rt-check/completeness/report) runs the preflight only: it reports what
the indexer actually sees and writes nothing. Even without it, a report is refused when
no DICOM is found, so the tool never emits a falsely-confident result on a tree it did
not understand.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .completeness import (
    DEFAULT_PROTOCOL,
    assign_timepoints,
    build_completeness,
    load_protocol,
    patient_completeness,
)
from .contract import build_verdict_payload, validate_payload
from .indexer import IndexResult, build_index
from .report_cohort import render_cohort_report
from .report_map import render_completeness_map
from .rt_integrity import build_rt_integrity, build_rt_rollup
from .synthetic import generate_longitudinal_cohort, generate_synthetic_cohort

LOG = logging.getLogger("DICOM_discovery")


def _preflight(idx: IndexResult, protocol=None) -> bool:
    """Print what the indexer saw; return True if it is safe to emit a report."""
    m = idx.manifest
    print("── Preflight ──────────────────────────────────────────")
    print(f"  files seen        : {m['n_files_seen']}")
    print(f"  DICOM indexed     : {m['n_dicom_indexed']}")
    print(f"  unreadable        : {m['n_unreadable']}")
    print(f"  patients          : {m['n_patients']}  (key source: {m['patient_id_source_counts']})")
    print(f"  studies           : {m['n_studies']}")
    print(f"  modalities        : {m['modalities']}")
    if not idx.table.empty:
        sample = (idx.table[["patient_id", "study_uid", "study_date", "modality"]]
                  .drop_duplicates("patient_id").head(3))
        print("  sample patients   :")
        for _, r in sample.iterrows():
            print(f"      {r['patient_id']}  study={r['study_uid'][:18]}…  date={r['study_date']}  {r['modality']}")
    ok = m["n_dicom_indexed"] > 0
    if not ok:
        print("  ⚠ no DICOM detected — refusing to emit a report (check --group-by / path).")
    if protocol is not None and not idx.table.empty:
        tagged = assign_timepoints(idx.table, protocol)
        mapped = set(tagged.loc[tagged["timepoint"] != "UNMAPPED", "patient_id"])
        n_all = tagged["patient_id"].nunique()
        print(f"  timepoints        : {n_all - len(mapped)}/{n_all} patient(s) with NO mapped study (UNMAPPED)")
        if len(mapped) == 0:
            print("  ⚠ no study could be placed on the protocol timeline — completeness map would be UNMAPPED.")
    print("───────────────────────────────────────────────────────")
    return ok


def _cmd_index(args) -> int:
    idx = build_index(args.root, patient_regexes=args.patient_regex, group_by=args.group_by, progress=True, workers=args.workers, cache=args.cache, assume_immutable=args.assume_immutable)
    _preflight(idx)
    if args.dry_run:
        return 0
    if args.out_csv:
        idx.table.to_csv(args.out_csv, index=False)
        print("Index table ->", args.out_csv)
    manifest_path = args.out_manifest or "index_manifest.json"
    Path(manifest_path).write_text(json.dumps(idx.manifest, indent=2), encoding="utf-8")
    print("Manifest ->", manifest_path)
    return 0


def _write_verdicts_json(rollup_df, manifest, protocol, path) -> None:
    """Emit the versioned, schema-validated verdict payload to ``path``."""
    payload = build_verdict_payload(rollup_df, manifest=manifest,
                                    protocol_name=getattr(protocol, "name", None))
    validate_payload(payload)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    print("Verdicts (JSON) ->", path)


def _cmd_rt_check(args) -> int:
    idx = build_index(args.root, patient_regexes=args.patient_regex, group_by=args.group_by, progress=True, workers=args.workers, cache=args.cache, assume_immutable=args.assume_immutable)
    ok = _preflight(idx)
    if args.dry_run:
        return 0
    if not ok:
        return 1
    df = build_rt_integrity(idx.table)
    rollup = build_rt_rollup(idx.table)

    # Presentation: per-patient verdict + scoped KPI (non-RT studies excluded).
    print("=== Per-patient verdict ===")
    print(rollup[["patient_id", "n_studies", "n_rt_studies", "rt_status", "fragmented", "reason"]].to_string(index=False))
    print("\n=== Patient verdict counts ===")
    print(rollup["rt_status"].value_counts().to_string())
    n_rt_studies = int((df["rt_status"] != "NOT_RT").sum())
    rt = df[df["rt_status"] != "NOT_RT"]
    print(f"\n=== RT studies only ({n_rt_studies} of {len(df)} studies; "
          f"{int((df['rt_status'] == 'NOT_RT').sum())} non-RT excluded) ===")
    print(rt["rt_status"].value_counts().to_string())

    if args.detail:
        cols = ["patient_id", "study_date", "rt_status", "findings"]
        print("\n=== Per-study detail (RT studies) ===")
        print(rt[cols].to_string(index=False))
    if args.out_csv:
        df.to_csv(args.out_csv, index=False)
        rollup.to_csv(args.out_csv.replace(".csv", "_by_patient.csv"), index=False)
        print(f"\nCSV (per study) -> {args.out_csv}")
        print(f"CSV (per patient) -> {args.out_csv.replace('.csv', '_by_patient.csv')}")
    if args.json_out:
        _write_verdicts_json(rollup, idx.manifest, None, args.json_out)
    return 0


def _cmd_completeness(args) -> int:
    protocol = load_protocol(args.protocol) if args.protocol else DEFAULT_PROTOCOL
    idx = build_index(args.root, patient_regexes=args.patient_regex, group_by=args.group_by, progress=True, workers=args.workers, cache=args.cache, assume_immutable=args.assume_immutable)
    ok = _preflight(idx, protocol)
    if args.dry_run:
        return 0
    if not ok:
        return 1
    state, hover, long = build_completeness(idx.table, protocol)
    print(patient_completeness(long).to_string(index=False))
    out = render_completeness_map(state, hover, long, args.out, protocol)
    print("\nMap ->", out)
    return 0


def _cmd_report(args) -> int:
    protocol = load_protocol(args.protocol) if args.protocol else DEFAULT_PROTOCOL
    idx = build_index(args.root, patient_regexes=args.patient_regex, group_by=args.group_by, progress=True, workers=args.workers, cache=args.cache, assume_immutable=args.assume_immutable)
    ok = _preflight(idx, protocol)
    if args.dry_run:
        return 0
    if not ok:
        return 1
    rt_study_df = build_rt_integrity(idx.table)
    rollup_df = build_rt_rollup(idx.table)
    comp_state, comp_hover, comp_long = build_completeness(idx.table, protocol)
    path = render_cohort_report(rt_study_df, rollup_df, comp_state, comp_hover, comp_long, idx.manifest, protocol, args.out, table=idx.table)
    print("Report ->", path)
    if args.json_out:
        _write_verdicts_json(rollup_df, idx.manifest, protocol, args.json_out)
    return 0


def _cmd_demo(args) -> int:
    base = Path(args.out_dir)
    rt_dir, long_dir, map_html = base / "synthetic_cohort", base / "longitudinal_cohort", base / "completeness_map.html"
    print(f"Generating synthetic cohorts under {base}/ ...")
    generate_synthetic_cohort(str(rt_dir))
    generate_longitudinal_cohort(str(long_dir))

    print("\n=== RT chain integrity (per patient/study) ===")
    rt_df, _idx = _build_rt(str(rt_dir))
    print(rt_df[["patient_id", "rt_status", "findings"]].to_string(index=False))

    print("\n=== Longitudinal completeness (observed vs expected) ===")
    c_idx = build_index(str(long_dir))
    state, hover, long = build_completeness(c_idx.table, DEFAULT_PROTOCOL)
    print(patient_completeness(long).to_string(index=False))
    out = render_completeness_map(state, hover, long, str(map_html), DEFAULT_PROTOCOL)
    print("\nDone. Open the map:\n  xdg-open", out)
    return 0


def _build_rt(root: str):
    idx = build_index(root)
    return build_rt_integrity(idx.table), idx


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dicom-discovery", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    def _common(sp):
        sp.add_argument("--root", required=True, help="directory tree to index")
        sp.add_argument("--group-by", choices=["dicom", "folder"], default="dicom",
                        help="patient key: DICOM PatientID tag (default) or top folder")
        sp.add_argument("--patient-regex", action="append", default=[],
                        help="fallback regex for patient id when PatientID is missing (repeatable)")
        sp.add_argument("--workers", type=int, default=8, help="parallel header-read threads (default 8)")
        sp.add_argument("--cache", default=None,
                        help="path to a persistent index cache; reuse unchanged files across runs")
        sp.add_argument("--assume-immutable", action="store_true",
                        help="warm-cache fast path FOR IMMUTABLE/APPEND-ONLY ARCHIVES ONLY: with "
                             "--cache, skip per-file stat and key the cache by path alone. New files "
                             "are still read; files modified in place are NOT re-read.")
        sp.add_argument("--dry-run", action="store_true", help="preflight only; write nothing")

    d = sub.add_parser("demo", help="generate synthetic cohorts and run everything")
    d.add_argument("--out-dir", default="examples")
    d.set_defaults(func=_cmd_demo)

    i = sub.add_parser("index", help="build the canonical DICOM index")
    _common(i)
    i.add_argument("--out-csv", default=None, help="write the index table to CSV")
    i.add_argument("--out-manifest", default=None, help="write the run manifest JSON (default: index_manifest.json)")
    i.set_defaults(func=_cmd_index)

    r = sub.add_parser("rt-check", help="RT chain integrity QC")
    _common(r)
    r.add_argument("--out-csv", default=None, help="optional CSV output (per-study + _by_patient.csv)")
    r.add_argument("--json", default=None, dest="json_out",
                   help="write the versioned, schema-validated verdict payload (JSON)")
    r.add_argument("--detail", action="store_true", help="also print the per-study RT table")
    r.set_defaults(func=_cmd_rt_check)

    c = sub.add_parser("completeness", help="observed-vs-expected completeness map")
    _common(c)
    c.add_argument("--protocol", default=None, help="protocol YAML (default: brain_rt_followup)")
    c.add_argument("--out", default="completeness_map.html", help="output HTML path")
    c.set_defaults(func=_cmd_completeness)

    rp = sub.add_parser("report", help="unified RT-integrity + completeness cohort report (HTML)")
    _common(rp)
    rp.add_argument("--protocol", default=None,
                    help="protocol YAML (default: brain_rt_followup)")
    rp.add_argument("--out", default="cohort_report.html",
                    help="output HTML path (default: cohort_report.html)")
    rp.add_argument("--json", default=None, dest="json_out",
                    help="also write the versioned, schema-validated verdict payload (JSON)")
    rp.set_defaults(func=_cmd_report)
    return p


def _force_utf8_stdio() -> None:
    """Make stdout/stderr tolerate the report's Unicode box-drawing/arrow characters.

    On Windows the default console encoding (e.g. cp1252) cannot encode the preflight's
    box-drawing characters, which would crash an otherwise-successful run at print time.
    Reconfiguring the streams to UTF-8 keeps the tool portable across environments; it is a
    harmless no-op where stdout is already UTF-8 (Linux/macOS/CI) or where the stream cannot
    be reconfigured (e.g. a capture wrapper that lacks ``reconfigure``).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


def main(argv=None) -> int:
    _force_utf8_stdio()
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
