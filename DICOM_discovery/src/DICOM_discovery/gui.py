"""Double-click launcher for the standalone binary — the only entry point that assumes no
terminal habit at all.

Someone who double-clicks `dicom-discovery.exe` passes no arguments. Reaching argparse there
would print "the following arguments are required: command", exit 2, and close the window
before it could be read. Instead: two folder pickers (the DICOM tree to check, where to write
the results), the ordinary scheduled ``job``, then the report opens in the browser.

Everything the desktop provides — the folder picker, the browser, the "press Enter" pause —
is injected, so the flow is testable without a display server, and the tool keeps working on
a machine where tkinter is missing (it falls back to typing the paths).
"""
from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from typing import Callable, List, Optional

from . import __version__

BANNER = f"""
DICOM_discovery {__version__} — cohort quality control for DICOM-RT data
Research Use Only — not a medical device, not a clinical safety check.

Two questions, then it runs on its own:
  1. which folder holds the DICOM data (it is only ever read),
  2. where the report should be written.
"""


def should_launch_gui(argv: List[str], frozen: bool) -> bool:
    """True when this invocation is a double-click (or an explicit ``--gui``).

    A frozen binary called *with* arguments (a scheduler, a support session) keeps the normal
    CLI; a bare ``dicom-discovery`` in a terminal keeps argparse's help.
    """
    if list(argv) == ["--gui"]:
        return True
    return frozen and not argv


def _ask_directory_tk(title: str) -> str:
    """Folder picker; falls back to a typed path when tkinter is unavailable."""
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:  # noqa: BLE001 - headless / trimmed Python: ask on the console
        return input(f"{title}\n> ").strip().strip('"')
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title=title) or ""
    finally:
        root.destroy()


def _wait_for_user() -> None:
    try:
        input("\nPress Enter to close this window…")
    except (EOFError, KeyboardInterrupt):
        pass


def run_interactive(
    ask_directory: Optional[Callable[[str], str]] = None,
    open_file: Optional[Callable[[str], None]] = None,
    wait_for_user: Optional[Callable[[], None]] = None,
) -> int:
    """Ask for the two folders, run the job, open the report. Returns the job's exit code."""
    ask_directory = ask_directory or _ask_directory_tk
    open_file = open_file or (lambda path: webbrowser.open(Path(path).resolve().as_uri()))
    wait_for_user = wait_for_user or _wait_for_user

    from .cli import _preflight, build_parser

    print(BANNER)
    root = ask_directory("Select the DICOM folder to check (read-only)")
    if not root:
        print("Annulé : aucun dossier DICOM sélectionné. / Cancelled: no DICOM folder chosen.")
        wait_for_user()
        return 1
    output = ask_directory("Select the folder where the results should be written")
    if not output:
        print("Annulé : aucun dossier de sortie sélectionné. / Cancelled: no output folder chosen.")
        wait_for_user()
        return 1

    print(f"\nDICOM : {root}\nSortie / output : {output}\n")
    args = build_parser().parse_args(["job", "--root", str(root), "--output-dir", str(output)])

    from .job import EXIT_OK, EXIT_PARTIAL, run_job

    code = run_job(args, _preflight)

    report = Path(output) / "latest" / "cohort_report.html"
    if code in (EXIT_OK, EXIT_PARTIAL) and report.is_file():
        print(f"\nRapport / report : {report}")
        try:
            open_file(str(report))
        except Exception as exc:  # noqa: BLE001 - no browser is not a failed run
            print(f"(impossible d'ouvrir le navigateur / could not open a browser: {exc})")
    else:
        print(f"\nAucun rapport produit (code {code}). / No report produced (exit {code}).")
        print(f"Voir / see: {Path(output) / 'last_run.json'}")
    wait_for_user()
    return code


def main() -> int:  # pragma: no cover - the frozen binary's entry point
    return run_interactive()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
