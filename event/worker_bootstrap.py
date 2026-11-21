"""Initialize DLL lookup before importing QGIS on Windows."""
import os
import runpy
from pathlib import Path
from runtime import LIBRARY

_dll_handles = []
if os.name == "nt":
    _dll_handles.append(os.add_dll_directory(str(LIBRARY / "bin")))
runpy.run_path(str(Path(__file__).with_name("gis_tools.py")), run_name="__main__")
