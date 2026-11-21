"""Locate the bundled conda-forge QGIS runtime on macOS and Windows."""
import os
from pathlib import Path

PREFIX = Path(os.environ.get("AI4S_QGIS_PREFIX", Path(__file__).resolve().parents[1] / ".tools/qgis"))
WINDOWS = os.name == "nt"
LIBRARY = PREFIX / "Library" if WINDOWS else PREFIX


def executable(name):
    if name == "python":
        return PREFIX / "python.exe" if WINDOWS else PREFIX / "bin/python"
    return LIBRARY / "bin" / (name + ".exe" if WINDOWS else name)


def environment():
    result = dict(os.environ)
    python_dir = LIBRARY / "python" if WINDOWS else PREFIX / "share/qgis/python"
    paths = [str(LIBRARY / "bin"), str(PREFIX), str(PREFIX / "Scripts")] if WINDOWS else [str(PREFIX / "bin")]
    result.update({
        "QGIS_PREFIX_PATH": str(LIBRARY),
        "PYTHONPATH": os.pathsep.join([str(python_dir), str(python_dir / "plugins")]),
        "QT_PLUGIN_PATH": os.pathsep.join([str(LIBRARY / "plugins"), str(LIBRARY / "qtplugins")]),
        "QT_QPA_PLATFORM": "offscreen",
        "PROJ_DATA": str(LIBRARY / "share/proj"),
        "GDAL_DATA": str(LIBRARY / "share/gdal"),
        "PATH": os.pathsep.join(paths + [os.environ.get("PATH", "")]),
        "PYTHONUTF8": "1",
    })
    return result
