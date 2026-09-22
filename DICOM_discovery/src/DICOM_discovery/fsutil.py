"""Crash-safe file writes for outputs that live on network shares (SMB / NFS / NAS).

A report, verdict file or index cache written in place is left truncated if the job is
killed, the share drops, or the volume fills mid-write — and a scheduled run on a NAS is
exactly where that happens. Every output is therefore written to a temporary file in the
*same directory*, flushed to disk, then atomically renamed over the target: readers see
either the previous complete file or the new complete file, never a partial one.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]"]

# Read once at import (single-threaded): os.umask can only be read by setting it, which is
# not thread-safe while worker threads may be creating files.
_UMASK = os.umask(0)
os.umask(_UMASK)


def _default_mode(target: Path) -> int:
    """Permission bits the file would get from a plain ``open()`` (keeps an existing file's).

    ``mkstemp`` creates 0600 files; left as-is, a report on a shared output folder would be
    unreadable by the clinicians it is meant for.
    """
    try:
        return target.stat().st_mode & 0o777
    except OSError:
        return 0o666 & ~_UMASK


def atomic_write_bytes(path: PathLike, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + fsync + ``os.replace``).

    On any failure the previous content of ``path`` is untouched and the temp file removed.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = _default_mode(target)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        with contextlib.suppress(OSError):  # some SMB mounts refuse chmod; content matters more
            os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_text(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    """Text counterpart of :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_csv(path: PathLike, df) -> None:
    """Write a DataFrame as CSV, atomically, **with a UTF-8 BOM**.

    The verdicts carry French text ("récupérer l'image de planification"). Excel opens a
    BOM-less UTF-8 CSV with the system codepage (cp1252 on a French Windows) and shows
    mojibake; the BOM makes it detect UTF-8. pandas/`csv` read it back unchanged
    (``utf-8-sig`` is stripped on read, and utf-8 readers tolerate the marker).
    """
    atomic_write_text(path, df.to_csv(index=False), encoding="utf-8-sig")
