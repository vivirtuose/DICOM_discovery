"""The CLI must not crash on consoles whose encoding cannot represent the preflight's
Unicode box-drawing characters (e.g. Windows cp1252). ``_force_utf8_stdio`` reconfigures
stdout/stderr to UTF-8 where possible and stays a safe no-op everywhere else.

TDD: written before ``_force_utf8_stdio`` existed.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import cli  # noqa: E402


class _FakeStream:
    """Stand-in for a text stream, optionally exposing/failing ``reconfigure``."""

    def __init__(self, has_reconfigure: bool = True, raises: bool = False):
        self.calls = []
        self._raises = raises
        if has_reconfigure:
            self.reconfigure = self._reconfigure  # type: ignore[assignment]

    def _reconfigure(self, **kwargs):
        if self._raises:
            raise ValueError("stream cannot be reconfigured")
        self.calls.append(kwargs)


def test_force_utf8_stdio_reconfigures_to_utf8(monkeypatch):
    out, err = _FakeStream(), _FakeStream()
    monkeypatch.setattr(cli.sys, "stdout", out)
    monkeypatch.setattr(cli.sys, "stderr", err)

    cli._force_utf8_stdio()

    assert out.calls and out.calls[0]["encoding"] == "utf-8"
    assert err.calls and err.calls[0]["encoding"] == "utf-8"


def test_force_utf8_stdio_tolerates_missing_reconfigure(monkeypatch):
    # A stream without ``reconfigure`` (e.g. some capture wrappers) must be skipped, not crash.
    monkeypatch.setattr(cli.sys, "stdout", _FakeStream(has_reconfigure=False))
    monkeypatch.setattr(cli.sys, "stderr", _FakeStream(has_reconfigure=False))
    cli._force_utf8_stdio()  # must not raise


def test_force_utf8_stdio_tolerates_failing_reconfigure(monkeypatch):
    # A stream whose ``reconfigure`` raises must be swallowed, not propagated.
    monkeypatch.setattr(cli.sys, "stdout", _FakeStream(raises=True))
    monkeypatch.setattr(cli.sys, "stderr", _FakeStream(raises=True))
    cli._force_utf8_stdio()  # must not raise
