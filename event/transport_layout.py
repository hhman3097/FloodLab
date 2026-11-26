"""Render transport evidence layers as an editable A3 QGIS project."""
import json
import shutil
import sys
from pathlib import Path

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (Qgis, QgsApplication, QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFillSymbol, QgsLayoutExporter,
    QgsLayoutItemLabel, QgsLayoutItemMap, QgsLayoutItemMapGrid, QgsLayoutItemPage,
    QgsLayoutItemPicture, QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize, QgsLineSymbol,
    QgsMarkerSymbol, QgsPalLayerSettings, QgsPrintLayout, QgsProject, QgsRectangle, QgsRendererCategory,
    QgsTextBufferSettings, QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling)


def render(folder, title, top):
    result = json.loads((folder / "analysis.json").read_text(encoding="utf-8"))
    if not result["segments"]:
        raise ValueError("No overlapping line segments were measured")
    project = QgsProject.instance()
    crs = QgsCoordinateReferenceSystem(result["crs"])
    project.setCrs(crs)
    project.setFilePathStorage(Qgis.FilePathType.Relative)

    def layer(file, name, symbol=None):
        item = QgsVectorLayer(str(folder / (file + ".geojson")), name, "ogr")
        if not item.isValid():
            raise ValueError(f"Invalid output layer: {file}")
        if symbol:
            item.renderer().setSymbol(symbol)
        project.addMapLayer(item)
        return item

    def fill(color, edge, width):
        return QgsFillSymbol.createSimple({"color": color, "outline_color": edge, "outline_width": width})

    def stroke(color, width):
        return QgsLineSymbol.createSimple({"line_color": color, "line_width": width})

    towns = layer("town_boundaries", "ICV municipality limits", fill("#f5f6f7", "#575e65", ".25"))
    coverage = layer("coverage", "Joint observation and road window", fill("#ffffff", "#444f57", ".26"))
    observed = layer("observed", "EMSR773 mapped flood and trace")
    observed.setRenderer(QgsCategorizedSymbolRenderer("evidence", [
        QgsRendererCategory("flooded_area", fill("#78bcda", "#82bad1", ".09"), "Flooded area"),
        QgsRendererCategory("flood_trace", fill("#d1e2ea", "#b3d0dd", ".09"), "Flood trace")]))
    roads = layer("assessed_roads", "Assessed road centerlines", stroke("#6b7176", ".26"))
    segments = layer("segments", "Planar road overlaps")
    segments.setRenderer(QgsCategorizedSymbolRenderer("evidence", [
        QgsRendererCategory("flooded_area", stroke("#a92b38", ".88"), "Flooded area overlap"),
        QgsRendererCategory("flood_trace", stroke("#ae591a", ".70"), "Flood trace overlap")]))

    def labels(vector, expression, size):
        pal = QgsPalLayerSettings()
        pal.fieldName, pal.isExpression = expression, True
        fmt = QgsTextFormat()
        fmt.setFont(QFont("Helvetica", size))
        fmt.setSize(size)
        fmt.setColor(QColor("#14252c"))
        buffer = QgsTextBufferSettings()
        buffer.setEnabled(True)
        buffer.setSize(.55)
        buffer.setColor(QColor("white"))
        fmt.setBuffer(buffer)
        pal.setFormat(fmt)
        vector.setLabeling(QgsVectorLayerSimpleLabeling(pal))
        vector.setLabelsEnabled(True)

    priority = result["priority"][:top]
    points = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"number": row["review_order"]},
         "geometry": {"type": "Point", "coordinates": [row["longitude"], row["latitude"]]}}
        for row in priority]}
    (folder / "review_points.geojson").write_text(json.dumps(points), encoding="utf-8")
    markers = layer("review_points", "Verification shortlist",
                    QgsMarkerSymbol.createSimple({"name": "circle", "color": "#ffffff",
                                                  "outline_color": "#29333b", "size": "2.3"}))
    labels(markers, 'to_string("number")', 11)
    labels(coverage, '"town"', 10)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Flood transport evidence")
    layout.pageCollection().page(0).setPageSize("A3", QgsLayoutItemPage.Landscape)
    layout.renderContext().setDpi(200)

    def label(value, x, y, w, h, size, bold=False, color="#14252c"):
        item = QgsLayoutItemLabel(layout)
        item.setText(value)
        font = QFont("Helvetica", size)
        font.setBold(bold)
        item.setFont(font)
        item.setFontColor(QColor(color))
        layout.addLayoutItem(item)
        item.attemptMove(QgsLayoutPoint(x, y))
        item.attemptResize(QgsLayoutSize(w, h))
        return item

    label(title, 16, 6, 395, 14, 23, True)
    label("TRANSPORT FIELD CHECK  /  EMSR773 AOI03 GRA v2  /  31 OCT 2024, 10:22 UTC", 16, 22, 385, 8, 10, True)
    main = QgsLayoutItemMap(layout)
    layout.addLayoutItem(main)
    main.attemptMove(QgsLayoutPoint(22, 40))
    main.attemptResize(QgsLayoutSize(269, 225))
    main.setCrs(crs)
    main.setLayers([markers, segments, roads, observed, coverage, towns])
    main.setKeepLayerSet(True)
    main.setFrameEnabled(True)
    xf = QgsCoordinateTransform(coverage.crs(), crs, project)
    extent = QgsRectangle(xf.transformBoundingBox(coverage.extent()))
    extent.scale(1.16)
    main.zoomToExtent(extent)
    step = 500 if extent.width() < 6000 else 2000
    grid = main.grid()
    grid.setEnabled(True)
    grid.setIntervalX(step)
    grid.setIntervalY(step)
    grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)
    grid.setFrameStyle(QgsLayoutItemMapGrid.ExteriorTicks)
    grid.setAnnotationEnabled(True)
    grid.setAnnotationPrecision(0)
    grid.setAnnotationFont(QFont("Helvetica", 8))
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Right)

    label("READING THE MAP", 311, 40, 98, 7, 11, True)
    for i, (name, color) in enumerate([
            ("Road overlap: flooded area", "#a92b38"), ("Road overlap: flood trace", "#ae591a"),
            ("Flooded area", "#78bcda"), ("Flood trace", "#b3ccd8"),
            ("Other assessed road", "#6b7176"), ("Mapped assessment boundary", "#444f57")]):
        y = 50 + i * 7
        swatch = label("", 311.5, y + (1.7 if i in (2, 3) else 3.1), 10, 3.6 if i in (2, 3) else .8, 10)
        swatch.setBackgroundEnabled(True)
        swatch.setBackgroundColor(QColor(color))
        label(name, 325, y, 82, 7, 10)
    label("FIELD CHECK ORDER", 311, 99, 98, 7, 11, True)
    for i, row in enumerate(result["priority"][:top]):
        name = row["road_ref"] or row["road_name"] or ("OSM " + row["osm_way"])
        if len(name) > 29:
            name = name[:26] + "..."
        label(f'{row["review_order"]:02d}  {name}  /  {row["overlap_m"]:.0f} m', 311, 109 + 5.9 * i, 99, 7, 9)
    label("DATA AND LIMITS", 311, 173, 97, 7, 11, True)
    label("EMSR773 / Horta Sud GRA v2\nICV municipality limits\nOpenStreetMap road snapshot: 28 Oct 2024\nEPSG:32630, metric centerline length", 311, 183, 98, 23, 9)
    label("Planar exposure only. Closure status unknown.\nBridge/tunnel tags require separate review.\nOutside the mapped assessment: unknown.", 311, 211, 99, 19, 9)
    scale = QgsLayoutItemScaleBar(layout)
    scale.setStyle("Single Box")
    scale.setLinkedMap(main)
    scale.setUnits(Qgis.DistanceUnit.Meters)
    scale.setUnitsPerSegment(250 if extent.width() < 2300 else step)
    scale.setNumberOfSegments(1)
    scale.setNumberOfSegmentsLeft(0)
    scale.setUnitLabel("m")
    layout.addLayoutItem(scale)
    scale.attemptMove(QgsLayoutPoint(311, 251))
    north = QgsLayoutItemPicture(layout)
    layout.addLayoutItem(north)
    north.attemptMove(QgsLayoutPoint(384, 233))
    north.attemptResize(QgsLayoutSize(13, 22))
    north_asset = folder / "north.svg"
    shutil.copyfile(Path(__file__).resolve().parent / "cartography_data/north.svg", north_asset)
    north.setPicturePath(str(north_asset.resolve()))
    label("N (grid)", 383, 225, 22, 7, 9)
    label("Use the CSV and line layer to locate individual candidates.  |  Research screening, not an official traffic advisory.",
          16, 279, 393, 9, 10)
    project.layoutManager().addLayout(layout)
    project.setFileName(str(folder / "transport.qgz"))
    if not project.write():
        raise RuntimeError("Could not save QGIS project")
    exporter = QgsLayoutExporter(layout)
    if exporter.exportToPdf(str(folder / "transport.pdf"), QgsLayoutExporter.PdfExportSettings()) != QgsLayoutExporter.Success:
        raise RuntimeError("Could not export PDF")
    image = QgsLayoutExporter.ImageExportSettings()
    image.dpi = 130
    if exporter.exportToImage(str(folder / "transport.png"), image) != QgsLayoutExporter.Success:
        raise RuntimeError("Could not export PNG")


if __name__ == "__main__":
    app = QgsApplication([], False)
    app.initQgis()
    render(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))
    QgsProject.instance().clear()
