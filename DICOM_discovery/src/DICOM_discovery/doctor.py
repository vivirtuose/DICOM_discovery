"""``dicom-discovery doctor`` — what environment am I actually running in, and will a run work?

The first question on any support request ("which Python? which pydicom? can the service
account read the share?") should be answerable with one command that the user can paste back.
It reads the environment and, when given ``--root`` / ``--output-dir``, *probes* them: an
unreadable share or an unwritable output folder is the most common reason a scheduled run
fails, and it is better to learn that here than from a 40-minute scan that writes nothing.

Exit code 0 when everything checked is usable, 1 when at least one check failed.
"""
from __future__ import annotations

import locale
import os
import platform
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from . import __version__
from .fsutil import atomic_write_bytes

DEPENDENCIES = ("pydicom", "pandas", "numpy", "plotly", "jsonschema", "yaml")

OK, BAD = "ok", "PROBLEM"


def _dependency_versions() -> List[Tuple[str, str]]:
    rows = []
    for name in DEPENDENCIES:
        try:
            module = __import__(name)
        except Exception as exc:  # noqa: BLE001 - a missing dep is exactly what we report
            rows.append((name, f"NOT INSTALLED ({type(exc).__name__})"))
            continue
        version = getattr(module, "__version__", None)
        if version is None:  # e.g. jsonschema deprecates __version__
            try:
                from importlib.metadata import version as _dist_version

                version = _dist_version("PyYAML" if name == "yaml" else name)
            except Exception:  # noqa: BLE001
                version = "unknown"
        rows.append((name, str(version)))
    return rows


def _windows_long_paths_enabled() -> Optional[bool]:
    """True/False on Windows (registry ``LongPathsEnabled``), None elsewhere/unknown.

    Deep DICOM exports routinely exceed the legacy 260-character limit; when long paths are
    off, those folders simply cannot be opened and land in ``unreadable_dirs``.
    """
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return bool(winreg.QueryValueEx(key, "LongPathsEnabled")[0])
    except Exception:  # noqa: BLE001
        return None


def _check_readable(root: str) -> Tuple[bool, str]:
    path = Path(root)
    if not path.exists():
        return False, f"DICOM root       : {path}  → not readable (does not exist / not mounted)"
    if not path.is_dir():
        return False, f"DICOM root       : {path}  → not readable (not a directory)"
    try:
        next(os.scandir(path), None)
    except OSError as exc:
        return False, f"DICOM root       : {path}  → not readable ({exc.strerror or exc})"
    return True, f"DICOM root       : {path}  → readable"


def _check_writable(output_dir: str) -> Tuple[bool, str]:
    path = Path(output_dir)
    probe = path / ".dicom-discovery-write-probe"
    try:
        path.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(probe, b"probe")
    except OSError as exc:
        return False, f"Output folder    : {path}  → not writable ({exc.strerror or exc})"
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return True, f"Output folder    : {path}  → writable"


def run_doctor(root: Optional[str] = None, output_dir: Optional[str] = None) -> int:
    """Print the environment report; return 0 if every check passed, else 1."""
    problems = 0
    print(f"DICOM_discovery  : {__version__}")
    print(f"Python           : {platform.python_version()} ({platform.python_implementation()}) — {sys.executable}")
    print(f"platform         : {platform.platform()} · {platform.machine()}")
    print(f"stdout encoding  : {getattr(sys.stdout, 'encoding', 'unknown')} · "
          f"filesystem: {sys.getfilesystemencoding()} · locale: {locale.getpreferredencoding(False)}")
    print(f"CPUs             : {os.cpu_count()}")

    long_paths = _windows_long_paths_enabled()
    if long_paths is False:
        problems += 1
        print("Windows paths    : PROBLEM — long paths (>260 chars) are disabled; deeply nested "
              "DICOM folders will be reported as unreadable. Enable LongPathsEnabled.")
    elif long_paths is True:
        print("Windows paths    : long paths enabled")

    print("Dependencies     :")
    for name, version in _dependency_versions():
        print(f"  {name:<11}      {version}")
        if "NOT INSTALLED" in version:
            problems += 1

    for check, value in ((_check_readable, root), (_check_writable, output_dir)):
        if value is None:
            continue
        good, line = check(value)
        print(line)
        problems += 0 if good else 1

    print("—" * 55)
    if problems:
        print(f"{BAD}: {problems} check(s) failed — see the lines above.")
        return 1
    print(f"{OK}: environment usable"
          + ("" if root or output_dir else " (pass --root/--output-dir to check a share too)"))
    return 0
