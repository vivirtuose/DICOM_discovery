"""The report's theme: the rules that let one :root block govern every colour on the page.

The stylesheets are Python string literals, so two failure modes are invisible until the page
is opened - a CSS escape eaten by Python, and a tint mixed against a hard-coded white that
survives a change of theme. Both are asserted here on the emitted CSS, not on the source.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from DICOM_discovery.report_cohort import VERDICT_COLORS, _styles  # noqa: E402
from DICOM_discovery.report_completeness import completeness_styles  # noqa: E402
from DICOM_discovery.report_overview import MOD_COLORS, overview_styles  # noqa: E402

ALL_CSS = _styles() + completeness_styles() + overview_styles()


def test_the_sort_arrows_survive_python_string_escaping():
    """`\\2191` in a plain Python string is an octal escape: the browser received a control
    character followed by "91" instead of an up arrow, on every sortable column."""
    css = _styles()
    assert 'content:" \\2191"' in css
    assert 'content:" \\2193"' in css
    assert not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", ALL_CSS), "a control character reached the CSS"


def test_no_tint_is_mixed_against_a_literal_colour():
    """Every "a hint of this hue" mixes into --mix-up and every "a readable shade" into
    --mix-down. A tint mixed against a literal white is the one thing that survives a change of
    theme: it painted near-white chips onto the midnight page."""
    literal_mixes = re.findall(r"color-mix\(in srgb,[^)]*?,\s*#[0-9a-fA-F]{3,6}\)", ALL_CSS)
    assert literal_mixes == [], f"tint mixed against a literal: {literal_mixes[:3]}"


def test_the_theme_declares_its_scheme_so_native_controls_follow():
    """Without color-scheme, the filter box, scrollbars and the <details> markers do not
    follow the page's palette."""
    root = _styles().split(":root{", 1)[1].split("}", 1)[0]
    assert "color-scheme:light" in root
    for token in ("--mix-up", "--mix-down", "--grad", "--glass"):
        assert token + ":" in root, f"{token} missing from :root"


def test_no_modality_hue_is_also_a_verdict_hue():
    """A page that shows modalities and verdicts side by side must never paint a modality in
    a verdict's colour - an orange RTSTRUCT bar next to an amber WARN slice reads as a claim."""
    verdict_hues = {c.lower() for c in VERDICT_COLORS.values()}
    clashes = {m: c for m, c in MOD_COLORS.items() if c.lower() in verdict_hues}
    assert clashes == {}, f"modality painted in a verdict colour: {clashes}"
