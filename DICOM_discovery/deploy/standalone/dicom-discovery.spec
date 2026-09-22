# PyInstaller spec — one self-contained executable for a machine with no Python at all.
#
#   pip install pyinstaller
#   pyinstaller --clean --noconfirm deploy/standalone/dicom-discovery.spec   (run from the package dir)
#
# Notes that matter:
#  * plotly ships plotly.min.js as package DATA; the report embeds it so the HTML opens
#    air-gapped. Without collect_all("plotly") the binary builds and then fails at render.
#  * jsonschema's format/spec resources live in jsonschema_specifications + referencing.
#  * tkinter is kept (the double-click folder picker); it falls back to typed paths when the
#    interpreter has no tkinter.
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

# Paths in a spec resolve against the spec's own directory (SPECPATH), not the CWD.
PACKAGE_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir, os.pardir))

datas, binaries, hiddenimports = [], [], []

for package in ("plotly",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

for package in ("jsonschema", "jsonschema_specifications", "referencing"):
    try:
        datas += collect_data_files(package)
    except Exception:  # optional at build time; jsonschema pulls the others in practice
        pass

hiddenimports += ["DICOM_discovery.gui", "tkinter", "tkinter.filedialog"]

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.path.join(PACKAGE_ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "ruff", "matplotlib", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="dicom-discovery",
    debug=False,
    strip=False,
    upx=False,          # UPX-packed binaries trip hospital antivirus far too often
    console=True,       # the run prints its progress; the launcher holds the window open
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
