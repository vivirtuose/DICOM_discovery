"""Guards on the NAS deployment files: the safety properties the deployment doc promises
(no network, read-only DICOM share, non-root, bounded resources) must not silently regress,
and the container's default command must be a valid ``dicom-discovery`` invocation."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from DICOM_discovery import __version__  # noqa: E402
from DICOM_discovery.cli import build_parser  # noqa: E402

NAS = _ROOT / "deploy" / "nas"


def _service() -> dict:
    compose = yaml.safe_load((NAS / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["dicom-discovery"]


def test_compose_isolates_the_container():
    svc = _service()
    assert svc["network_mode"] == "none"          # patient data cannot leave the container
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert not str(svc["user"]).startswith("0")   # never root
    assert svc["mem_limit"] and svc["cpus"]


def test_compose_mounts_the_dicom_share_read_only():
    data = [v for v in _service()["volumes"] if ":/data" in v]
    assert len(data) == 1 and data[0].endswith(":/data:ro")


def test_compose_command_parses_with_the_cli():
    cmd = [re.sub(r"\$\{[A-Z_]+:-([^}]*)\}", r"\1", str(a)) for a in _service()["command"]]
    args = build_parser().parse_args(cmd)
    assert args.command == "job" and args.root == "/data" and args.output_dir == "/output"


def test_dockerfile_default_command_is_the_scheduled_job():
    text = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    cmd = json.loads(re.search(r"^CMD (\[.*\])$", text, re.M).group(1))
    args = build_parser().parse_args(cmd)
    assert args.command == "job" and args.root == "/data" and args.output_dir == "/output"
    assert re.search(r"^USER (?!root)\S+$", text, re.M)


def test_deployment_files_reference_the_current_version():
    assert f"DD_VERSION:-{__version__}" in (NAS / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"DD_VERSION={__version__}" in (NAS / ".env.example").read_text(encoding="utf-8")


def test_offline_installer_never_reaches_an_index():
    script = (NAS / "install-offline.sh").read_text(encoding="utf-8")
    installs = [ln for ln in script.splitlines() if "pip install" in ln and not ln.lstrip().startswith("#")]
    assert installs and all("--no-index" in ln for ln in installs)
