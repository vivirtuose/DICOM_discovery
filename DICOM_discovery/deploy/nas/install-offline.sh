#!/bin/sh
# Offline install of DICOM_discovery from the wheelhouse bundle — for a Linux server (or a
# NAS with Python >= 3.9) that has NO internet access. Nothing is downloaded: every wheel,
# including a recent pip, ships in ./wheelhouse next to this script.
#
#   sh install-offline.sh [DEST]          # default DEST=/opt/dicom-discovery
#   PYTHON=python3.11 sh install-offline.sh ~/dicom-discovery
#
# The bundle must match the target: same Python minor version (bundle name *-py3.X-*) on
# x86_64 Linux (glibc >= 2.17: RHEL/Rocky 8+, Debian 10+, Ubuntu 20.04+).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PYTHON:-python3}
DEST=${1:-/opt/dicom-discovery}
WHEELS="$HERE/wheelhouse"

[ -d "$WHEELS" ] || { echo "wheelhouse/ not found next to $0" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { echo "Python >= 3.9 required ($PY is $("$PY" -V 2>&1))" >&2; exit 1; }

"$PY" -m venv "$DEST/venv"
# Old distro pips cannot install manylinux_2_17 wheels (numpy/pandas): upgrade pip first,
# from the bundle.
"$DEST/venv/bin/python" -m pip install --no-index --find-links "$WHEELS" --upgrade pip
"$DEST/venv/bin/python" -m pip install --no-index --find-links "$WHEELS" DICOM_discovery
if [ -f "$HERE/protocol.brain_rt_followup.yaml" ]; then
  cp "$HERE/protocol.brain_rt_followup.yaml" "$DEST/"
fi

"$DEST/venv/bin/dicom-discovery" --help >/dev/null
echo "Installed: $DEST/venv/bin/dicom-discovery"
echo "Check a share without writing anything:"
echo "  $DEST/venv/bin/dicom-discovery job --root /mnt/dicom --output-dir /srv/qc --dry-run"
