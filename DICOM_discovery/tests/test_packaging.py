"""Distribution metadata: what a package needs to be installable from PyPI and to render
correctly there. These are guards, not style checks — each one corresponds to something a
user sees (a broken link on the project page, a missing licence, an unclear Python support
range) or to a release that cannot be published."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REPO = _ROOT.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.9 / 3.10 — tomli is not a dependency, so fall back to a tiny regex reader
    tomllib = None

from DICOM_discovery import __version__  # noqa: E402

PYPROJECT = _ROOT / "pyproject.toml"


def _project() -> dict:
    if tomllib is not None:
        return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    text = PYPROJECT.read_text(encoding="utf-8")
    block = text.split("[project]", 1)[1].split("\n[", 1)[0]
    return {"_raw": block, "_text": text}


def _raw() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def test_version_is_single_sourced():
    assert f'version = "{__version__}"' in _raw()


def test_supported_python_versions_are_advertised():
    """The classifiers are what PyPI shows; they must match the CI matrix (3.9-3.14)."""
    raw = _raw()
    for minor in range(9, 15):
        assert f'"Programming Language :: Python :: 3.{minor}"' in raw, f"3.{minor}"


def test_project_urls_point_somewhere_useful():
    raw = _raw()
    for label in ("Homepage", "Source", "Changelog", "Issues"):
        assert f"{label} =" in raw, label


def test_license_file_is_shipped():
    """`license = MIT` and a README badge are not a licence; the file must exist and be
    referenced so it lands in the sdist and the wheel."""
    root_licence, package_licence = _REPO / "LICENSE", _ROOT / "LICENSE"
    assert root_licence.exists(), "repository LICENSE missing"
    # A build only sees this subdirectory, so the package needs its own identical copy.
    assert package_licence.exists(), "package LICENSE missing (wheel would ship none)"
    assert root_licence.read_text(encoding="utf-8") == package_licence.read_text(encoding="utf-8")
    assert "MIT" in root_licence.read_text(encoding="utf-8")
    assert 'license-files = ["LICENSE"]' in _raw()


def test_readme_has_no_relative_links():
    """The README is the PyPI project page: relative links 404 there."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    relative = [m.group(0) for m in re.finditer(r"\]\((?!https?://|#)[^)]+\)", readme)]
    assert not relative, f"relative links break on PyPI: {relative}"


def test_release_workflow_checks_the_tag_matches_the_version():
    """A tag that disagrees with pyproject would publish a mislabelled release."""
    workflow = (_REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "refs/tags/v" in workflow or "GITHUB_REF_NAME" in workflow
    assert "does not match" in workflow


def test_pypi_publish_is_never_automatic_on_push():
    """Publishing is irreversible: it must require an explicit, human action (a published
    GitHub release or a manual dispatch), never a push or a tag alone."""
    publish = (_REPO / ".github" / "workflows" / "publish-pypi.yml").read_text(encoding="utf-8")
    trigger_block = publish.split("jobs:", 1)[0]
    assert "workflow_dispatch" in trigger_block
    assert "push:" not in trigger_block
    assert "id-token: write" in publish  # Trusted Publishing, no long-lived API token
