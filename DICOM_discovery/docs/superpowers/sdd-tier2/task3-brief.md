# Task 3 — `report` CLI command + preflight, README + CHANGELOG

## Goal
Wire the unified cohort report into the CLI as `dicom-discovery report`, respecting the
`--dry-run` preflight, and document it. This completes Tier 2 (Spec Section C of
docs/superpowers/specs/2026-06-17-dicom-discovery-v0.6-design.md).

TDD: write the failing CLI test first (invoke the command on a cohort dir → assert the HTML is
written; invoke with --dry-run → assert NOTHING is written and exit 0), run, then implement the
handler until green.

## Prereqs you can rely on (do NOT redo)
- Task 1 (commit 5e6c1f7): import fixed, 6 data-assembly functions tested.
- Task 2 (commit d19bf86): `render_cohort_report(rt_study_df, rollup_df, comp_state, comp_hover,
  comp_long, manifest, protocol, out_path) -> str` produces a self-contained HTML, tested.
- The CLI wiring pattern is in docs/superpowers/sdd-tier2/task0-recon.md — READ IT. The closest
  analog is `_cmd_completeness` (cli.py:114) registered at cli.py:191. `_preflight(idx, protocol)`
  is at cli.py:37, `_common(sp)` attaches the shared flags.

## Scope — IN
1. **Add the `report` subcommand to `src/DICOM_discovery/cli.py`**, mirroring `completeness`:
   - `r = sub.add_parser("report", help="unified RT-integrity + completeness cohort report (HTML)")`
   - `_common(r)` (gives --root --group-by --patient-regex --workers --cache --assume-immutable --dry-run)
   - `r.add_argument("--protocol", default=None, ...)`
   - `r.add_argument("--out", default="cohort_report.html", ...)`
   - `r.set_defaults(func=_cmd_report)`
   - Handler `_cmd_report(args) -> int` body, IN THIS ORDER (match _cmd_completeness):
     load protocol → `idx = build_index(args.root, patient_regexes=args.patient_regex,
     group_by=args.group_by, progress=True, workers=args.workers, cache=args.cache,
     assume_immutable=args.assume_immutable)` → `ok = _preflight(idx, protocol)` →
     `if args.dry_run: return 0` → `if not ok: return 1` → compute
     `rt_study_df = build_rt_integrity(idx.table)`, `rollup_df = build_rt_rollup(idx.table)`,
     `comp_state, comp_hover, comp_long = build_completeness(idx.table, protocol)` →
     `path = render_cohort_report(rt_study_df, rollup_df, comp_state, comp_hover, comp_long,
     idx.manifest, protocol, args.out)` → print the written path → `return 0`.
     (Confirm the exact arg order/names of render_cohort_report and build_completeness against
     the code — recon lists them; the code is authoritative.)
   - Add the needed imports to cli.py (build_rt_integrity, build_rt_rollup from .rt_integrity;
     build_completeness from .completeness; render_cohort_report from .report_cohort) following
     the existing import style.
2. **Test the CLI wiring** (new test, follow existing CLI/test conventions — check if there is a
   tests/test_cli.py or how other commands are tested; if commands are tested via the parser +
   handler, do the same). Cover:
   - `report --root <cohort> --out <tmp>/r.html` writes the file and returns 0.
   - `report --root <cohort> --dry-run` writes NO html file and returns 0 (preflight respected).
   Use the synthetic or longitudinal cohort builder from the fixtures. Keep render cost in mind
   (the full render embeds plotly ~3.5MB and is slow once) — if a test renders, reuse a fixture or
   keep it to a single render; the dry-run test must NOT render.
3. **README**: add a `report` entry to the commands documentation, with an example invocation and
   a one-line description (unified RT-integrity + completeness, self-contained HTML, air-gapped,
   RUO). Match the existing README style for the other commands.
4. **CHANGELOG.md**: add a line under the v0.6.0 / unreleased section for the unified cohort report
   (`report` command). Match existing CHANGELOG style.

## Scope — OUT
- No changes to report_cohort.py rendering or data-assembly (Tasks 1-2 own it) beyond what is
  strictly needed to call it. If you find a real bug calling it, note it as a concern.
- No real-NAS benchmark (separate follow-up).

## Constraints (non-negotiable)
- Preserve the `--dry-run` = preflight-that-refuses-to-emit-on-empty-scan contract: dry-run must
  not write output; an empty/misunderstood scan must not silently emit (return 1).
- RUO only. Self-contained HTML (already guaranteed by render_cohort_report). Python 3.8.
- All tests stay green; `ruff check src synth tests` clean.
- GOTCHA: NFS-latent repo — never run overlapping/background pytest; one foreground run.
- Do NOT commit generated *.html/*.csv (.gitignore blocks; tests use tmp_path).

## Verification (paste real output into report)
- `python -m pytest -q` (all green, count) ; `python -m ruff check src synth tests` (clean).
- Actually RUN the command end-to-end once on the synthetic cohort and confirm an HTML file is
  produced (and that `--dry-run` produces none). Paste the command + the printed output path.
  Use `dicom-discovery` console script OR `python -m DICOM_discovery report ...`.

## Commit
One atomic commit on `tier2-cohort-report`, imperative message matching existing history, footer
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. cli.py + tests + README + CHANGELOG only.

## Done when
`dicom-discovery report` renders a self-contained HTML on the synthetic cohort, preflight/dry-run
respected, CLI test green, README + CHANGELOG updated, full suite green, ruff clean, one atomic commit.

## Report to
docs/superpowers/sdd-tier2/task3-report.md (full detail). Return only: status, commit range,
one-line test summary, concerns.
