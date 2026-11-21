"""Render the existing Lucide map symbol to native application icon assets."""
from pathlib import Path
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QGuiApplication, QImage, QPainter
from qgis.PyQt.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent / "assets"
app = QGuiApplication([])
renderer = QSvgRenderer(str(HERE / "icon.svg"))
iconset = HERE / "icon.iconset"
iconset.mkdir(exist_ok=True)
for size in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        image = QImage(size * scale, size * scale, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        suffix = "@2x" if scale == 2 else ""
        image.save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
        if size == 512 and scale == 2:
            image.save(str(HERE / "icon.png"))
