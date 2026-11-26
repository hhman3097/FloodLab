"""A QGIS A3 layout for freshly segmented Sentinel-1 flood candidates."""
import json
import shutil
import sys
from pathlib import Path

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (Qgis, QgsApplication, QgsContrastEnhancement, QgsCoordinateReferenceSystem,
    QgsFillSymbol, QgsLayoutExporter, QgsLayoutItemLabel, QgsLayoutItemMap, QgsLayoutItemMapGrid,
    QgsLayoutItemPage, QgsLayoutItemPicture, QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize,
    QgsPalettedRasterRenderer, QgsPrintLayout, QgsProject, QgsRasterLayer, QgsRectangle, QgsSingleBandGrayRenderer,
    QgsVectorLayer)


def render(folder):
    result = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    before, after = result["dates"]
    images = folder.parent.parent / "inputs" / result["input"]
    project = QgsProject.instance()
    crs = QgsCoordinateReferenceSystem(result["crs"])
    project.setCrs(crs)
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    post = QgsRasterLayer(str(images / f"s1-{after}.tif"), f"Sentinel-1 VV | {after}")
    prior = QgsRasterLayer(str(images / f"s1-{before}.tif"), f"Sentinel-1 VV | {before}")
    for raster in (prior, post):
        if not raster.isValid():
            raise ValueError("Missing or invalid Sentinel-1 context")
        p = raster.dataProvider()
        style = QgsContrastEnhancement(p.dataType(1))
        style.setContrastEnhancementAlgorithm(QgsContrastEnhancement.StretchToMinimumMaximum)
        style.setMinimumValue(-25)
        style.setMaximumValue(-4)
        renderer = QgsSingleBandGrayRenderer(p, 1)
        renderer.setContrastEnhancement(style)
        raster.setRenderer(renderer)
        project.addMapLayer(raster)
    candidate = QgsVectorLayer(str(folder / "candidate.gpkg") + "|layername=candidate", "Candidate inundation", "ogr")
    if not candidate.isValid() or not candidate.featureCount():
        raise ValueError("Candidate polygons unavailable")
    candidate.renderer().setSymbol(QgsFillSymbol.createSimple({"color": "53,197,234,255", "outline_style":"no"}))
    project.addMapLayer(candidate)
    agreement = QgsRasterLayer(str(folder / "agreement.tif"), "Earlier EMS | Spatial agreement (not validation)")
    if not agreement.isValid():
        raise ValueError("Comparison raster unavailable")
    agreement.setRenderer(QgsPalettedRasterRenderer(agreement.dataProvider(), 1, [
        QgsPalettedRasterRenderer.Class(0, QColor("transparent"), "Neither"),
        QgsPalettedRasterRenderer.Class(1, QColor("#16866a"), "Both sources"),
        QgsPalettedRasterRenderer.Class(2, QColor("#e38d36"), "Radar only"),
        QgsPalettedRasterRenderer.Class(3, QColor("#835baa"), "EMS only"),
    ]))
    project.addMapLayer(agreement)
    project.layerTreeRoot().findLayer(agreement.id()).setItemVisibilityChecked(False)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Radar-derived flood candidates")
    layout.pageCollection().page(0).setPageSize("A3", QgsLayoutItemPage.Landscape)
    layout.renderContext().setDpi(175)

    def label(value, x, y, w, h, size, bold=False):
        item = QgsLayoutItemLabel(layout)
        item.setText(value)
        font = QFont("Helvetica", size)
        font.setBold(bold)
        item.setFont(font)
        item.setFontColor(QColor("#162630"))
        layout.addLayoutItem(item)
        item.attemptMove(QgsLayoutPoint(x, y))
        item.attemptResize(QgsLayoutSize(w, h))
        return item

    def map_item(layers, x, y, w, h):
        item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(item)
        item.attemptMove(QgsLayoutPoint(x, y))
        item.attemptResize(QgsLayoutSize(w, h))
        item.setCrs(crs)
        item.setLayers(layers)
        item.setKeepLayerSet(True)
        item.setFrameEnabled(True)
        # Match the main map to the fixed study raster, not only the selected patches.
        item.zoomToExtent(QgsRectangle(post.extent()))
        return item

    label("Horta Sud | Sentinel-1 flood-change candidates", 16, 7, 392, 14, 23, True)
    label(f"VALENCIA, SPAIN  /  {before} to {after}  /  VV + VH, ORBIT {result['relative_orbit']}", 16, 22, 391, 9, 11)
    main = map_item([candidate, post], 25, 42, 264, 208)
    grid = main.grid()
    grid.setEnabled(True)
    grid.setIntervalX(2500)
    grid.setIntervalY(2500)
    grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)
    grid.setFrameStyle(QgsLayoutItemMapGrid.ExteriorTicks)
    grid.setAnnotationEnabled(True)
    grid.setAnnotationPrecision(0)
    grid.setAnnotationFont(QFont("Helvetica", 8))
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Right)
    label("PRE-EVENT VV | same display stretch", 307, 35, 98, 9, 10, True)
    map_item([prior], 308, 46, 95, 73)
    swatch = label("", 309, 130, 11, 5, 10)
    swatch.setBackgroundEnabled(True)
    swatch.setBackgroundColor(QColor("#35c5ea"))
    label("Candidate inundation", 323, 128, 82, 8, 11, True)
    label("Grey background: post-event VV", 309, 138, 98, 8, 10)
    label(f"{result['candidate_km2']:.2f} km2  /  {result['candidate_pixels']:,} pixels", 309, 151, 99, 10, 15, True)
    label("Selected municipal overlaps", 309, 169, 98, 8, 10, True)
    for i, row in enumerate(result["towns"][:4]):
        label(f"{row['town']}  /  {row['candidate_km2']:.2f} km2", 309, 178 + 6*i, 98, 7, 10)
    label("Source: Copernicus Sentinel-1 RTC\nAccess: Microsoft Planetary Computer\n20 m grid  |  WGS 84 / UTM 30N", 309, 207, 97, 19, 10)
    label("Radar-change candidates only.\nNot confirmed flood extent or depth.", 309, 232, 96, 16, 10, True)
    scale = QgsLayoutItemScaleBar(layout)
    scale.setStyle("Single Box")
    scale.setLinkedMap(main)
    scale.setUnits(Qgis.DistanceUnit.Kilometers)
    scale.setUnitsPerSegment(1)
    scale.setNumberOfSegments(3)
    scale.setNumberOfSegmentsLeft(0)
    scale.setUnitLabel("km")
    layout.addLayoutItem(scale)
    scale.attemptMove(QgsLayoutPoint(26, 262))
    north_file = folder / "north.svg"
    shutil.copyfile(Path(__file__).resolve().parent / "cartography_data/north.svg", north_file)
    north = QgsLayoutItemPicture(layout)
    layout.addLayoutItem(north)
    north.attemptMove(QgsLayoutPoint(265, 258))
    north.attemptResize(QgsLayoutSize(11, 18))
    north.setPicturePath(str(north_file.resolve()))
    label("N (grid)", 249, 263, 19, 8, 9)
    label("Same-orbit backscatter change  |  Observed window shown, not a flood peak reconstruction.", 18, 282, 390, 8, 9)
    project.layoutManager().addLayout(layout)
    if not project.write(str(folder / "map.qgz")):
        raise RuntimeError("QGIS project save failed")
    exporter = QgsLayoutExporter(layout)
    img = QgsLayoutExporter.ImageExportSettings()
    img.dpi = 130
    if exporter.exportToPdf(str(folder / "map.pdf"), QgsLayoutExporter.PdfExportSettings()) != QgsLayoutExporter.Success or \
       exporter.exportToImage(str(folder / "map.png"), img) != QgsLayoutExporter.Success:
        raise RuntimeError("QGIS export failed")
    project.clear()


if __name__ == "__main__":
    app = QgsApplication([], False)
    app.initQgis()
    render(Path(sys.argv[1]))
