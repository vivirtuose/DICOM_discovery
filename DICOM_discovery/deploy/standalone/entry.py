"""Entry point of the standalone binary (PyInstaller).

Deliberately thin: the frozen executable behaves exactly like the installed CLI, except that
starting it with no arguments — a double-click — hands over to the interactive launcher
(see DICOM_discovery.gui).
"""
import sys

from DICOM_discovery.cli import main

if __name__ == "__main__":
    sys.exit(main())
