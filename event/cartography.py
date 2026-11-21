"""Build editable QGIS layouts from the fixed analysis products."""
import argparse
import json
import os
from pathlib import Path

from qgis.PyQt.QtCore import QSizeF
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem,
    QgsFillSymbol, QgsLayoutExporter, QgsLayoutItemLabel,
    QgsLayoutItemMap, QgsLayoutItemMapGrid, QgsLayoutItemMapOverview, QgsLayoutItemPage,
    QgsLayoutItemScaleBar, QgsLayoutItemLegend, QgsLayoutItemPicture,
    QgsLayoutPoint, QgsLayoutSize, QgsPalLayerSettings,
    QgsPrintLayout, QgsProject, QgsRasterLayer, QgsRectangle,
    QgsTextBufferSettings, QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling,
    QgsCoordinateTransform, QgsLayerTreeLayer, QgsLineSymbol,
)

ROOT = Path(__file__).resolve().parent
INK = "#183036"
MUTED = "#496268"
COLORS = {"candidate": "#55d8ee", "stable-water": "#274e82", "ems-reference": "#f2c57c",
          "agreement-both": "#46d1c5", "agreement-sar": "#fcac5d", "agreement-ems": "#bb91d9"}


def build(target, mode, without_ems=False):
    target.mkdir(parents=True, exist_ok=True)
    app = QgsApplication([], False)
    app.initQgis()
    project = QgsProject.instance()
    crs = QgsCoordinateReferenceSystem("EPSG:32630")
    project.setCrs(crs)
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    project.setTitle("Valencia 2024 | Flood observation and evidence")
    metrics = json.loads((ROOT / "products/analysis.json").read_text())

    provenance = json.loads((ROOT / "inputs/provenance.json").read_text())
    base = QgsRasterLayer(str(ROOT / "inputs" / provenance["image_context"]["file"]), "Sentinel-2 RGB • 26 Oct 2024")
    if not base.isValid():
        raise RuntimeError("Invalid cartographic base")
    project.addMapLayer(base)
    layers = {}
    titles = {
        "candidate": "SAR change candidate", "stable-water": "Stable dark water proxy",
        "ems-reference": "EMS delineation | 31 Oct", "study-area": "Analysis boundary",
        "agreement-both": "Both sources", "agreement-sar": "SAR only",
        "agreement-ems": "EMS only",
    }
    if without_ems:
        if mode != "candidate":
            raise ValueError("EMS is required for the source comparison")
        for key in ("ems-reference", "agreement-both", "agreement-sar", "agreement-ems"):
            del titles[key]
    for key, title in titles.items():
        layer = QgsVectorLayer(str(ROOT / "products" / f"{key}.geojson"), title, "ogr")
        if not layer.isValid() or layer.crs() != crs:
            raise RuntimeError(f"Invalid vector layer or CRS: {key}")
        if key in ("ems-reference", "study-area"):
            properties = {"style": "no", "outline_color": COLORS.get(key, "#f6f6ef"),
                          "outline_width": ".27", "outline_style": "dash" if key == "study-area" else "solid"}
        else:
            color = QColor(COLORS[key])
            color.setAlpha(214 if key != "stable-water" else 150)
            properties = {"color": f"{color.red()},{color.green()},{color.blue()},{color.alpha()}",
                          "outline_style": "no"}
        layer.renderer().setSymbol(QgsFillSymbol.createSimple(properties))
        project.addMapLayer(layer)
        layers[key] = layer
    for path in sorted((ROOT / "products").glob("*.tif")):
        if without_ems and path.stem == "agreement":
            continue
        layer = QgsRasterLayer(str(path), path.stem)
        if not layer.isValid():
            raise RuntimeError(f"Invalid result: {path.name}")
        project.addMapLayer(layer)
        project.layerTreeRoot().findLayer(layer.id()).setItemVisibilityChecked(False)

    cartography_path = target / "places.geojson"
    points = [("València", -.376, 39.469), ("Torrent", -.466, 39.437),
              ("Paiporta", -.419, 39.428), ("Catarroja", -.400, 39.404),
              ("Silla", -.411, 39.362), ("Albufera", -.335, 39.342),
              ("Sollana", -.382, 39.278), ("Almussafes", -.415, 39.290)]
    cartography_path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]},
         "properties": {"name": name}} for name, x, y in points]}, ensure_ascii=False))
    places = QgsVectorLayer(str(cartography_path), "Approximate locality labels", "ogr")
    project.addMapLayer(places)
    places.renderer().symbol().setSize(1.25)
    places.renderer().symbol().setColor(QColor("#f9f2da"))
    pal = QgsPalLayerSettings()
    pal.fieldName = "name"
    fmt = QgsTextFormat()
    fmt.setFont(QFont("Helvetica", 10))
    fmt.setSize(10)
    fmt.setColor(QColor("#ffffff"))
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(.65)
    buffer.setColor(QColor("#182e31"))
    fmt.setBuffer(buffer)
    pal.setFormat(fmt)
    places.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    places.setLabelsEnabled(True)
    locator_path = target / "locator.geojson"
    locator_path.write_text(json.dumps({
        "type":"FeatureCollection",
        "features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[-.376,39.469]},
                     "properties":{"name":"Study area"}}]}, ensure_ascii=False))
    locator = QgsVectorLayer(str(locator_path), "Study area locator", "ogr")
    locator.renderer().symbol().setColor(QColor("#d73b3b"))
    locator.renderer().symbol().setSize(3.0)
    project.addMapLayer(locator)
    countries = QgsVectorLayer(
        str(ROOT / "cartography_data/ne_50m_admin_0_countries.shp"),
        "Natural Earth countries", "ogr")
    if not countries.isValid():
        raise RuntimeError("Natural Earth locator layer missing")
    project.addMapLayer(countries)
    for layer in (countries, locator):
        project.layerTreeRoot().findLayer(layer.id()).setItemVisibilityChecked(False)
    countries.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color":"235,238,239,255", "outline_color":"150,160,162,255",
        "outline_width":".15"}))

    def label(layout, text, x, y, w, h, size=10, bold=False, color=INK):
        item = QgsLayoutItemLabel(layout)
        item.setText(text)
        f = QFont("Helvetica")
        f.setPointSizeF(size)
        f.setBold(bold)
        item.setFont(f)
        item.setFontColor(QColor(color))
        item.setMargin(0)
        layout.addLayoutItem(item)
        item.attemptMove(QgsLayoutPoint(x, y))
        item.attemptResize(QgsLayoutSize(w, h))
        return item

    def map_item(layout, x, y, w, h, items, extent):
        item = QgsLayoutItemMap(layout)
        layout.addLayoutItem(item)
        item.attemptMove(QgsLayoutPoint(x, y))
        item.attemptResize(QgsLayoutSize(w, h))
        item.setCrs(crs)
        item.setLayers(items)
        item.setKeepLayerSet(True)
        item.setKeepLayerStyles(True)
        item.zoomToExtent(extent)
        item.setFrameEnabled(True)
        item.setFrameStrokeColor(QColor("#445a5f"))
        return item

    def add_legend(layout, linked_map, x, y, w, h, keys):
        legend = QgsLayoutItemLegend(layout)
        legend.setTitle("")
        legend.setLinkedMap(linked_map)
        legend.setAutoUpdateModel(False)
        root = legend.model().rootGroup()
        root.removeAllChildren()
        for key in keys:
            root.addChildNode(QgsLayerTreeLayer(layers[key]))
        legend.legendSettings().setFontColor(QColor(INK))
        legend.legendSettings().setSymbolSize(QSizeF(8, 3.4))
        legend.setFrameEnabled(False)
        layout.addLayoutItem(legend)
        legend.attemptMove(QgsLayoutPoint(x, y))
        legend.attemptResize(QgsLayoutSize(w, h))
        return legend

    def add_north_arrow(layout, main, x, y):
        label(layout, "N", x + 2.65, y, 5, 4, 10, True)
        picture = QgsLayoutItemPicture(layout)
        picture.setPicturePath(str(ROOT / "cartography_data/north.svg"),
                               QgsLayoutItemPicture.FormatSVG)
        picture.setLinkedMap(main)
        picture.setId("Grid north")
        layout.addLayoutItem(picture)
        picture.attemptMove(QgsLayoutPoint(x, y + 4))
        picture.attemptResize(QgsLayoutSize(8, 12))
        return picture

    def add_extent_frame(main, inset):
        overview = QgsLayoutItemMapOverview("Detail A footprint", main)
        overview.setLinkedMap(inset)
        overview.setFrameSymbol(QgsFillSymbol.createSimple({
            "style": "no", "outline_color": "#172b30", "outline_width": ".48"
        }))
        overview.setEnabled(True)
        main.overviews().addOverview(overview)

    def sheet(name, review):
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName(name)
        layout.pageCollection().page(0).setPageSize("A3", QgsLayoutItemPage.Landscape)
        label(layout, "AI FOR SCIENCE  /  VALENCIA FLOOD MAP SERIES", 20, 9, 260, 7, 10, True, MUTED)
        label(layout, "02 / 02" if review else "01 / 02", 379, 9, 24, 7, 10, True, MUTED)
        label(layout, "Valencia: SAR–EMS flood comparison" if review else
              "Valencia: Surface-water change candidates", 20, 19, 382, 15, 24, True)
        label(layout, "October 2024 flood  |  SAR and EMS observed on different dates" if review else
              "October 2024 flood  |  Multi-date Sentinel-1 observations",
              20, 36, 382, 9, 12, False, MUTED)
        primary = ["agreement-both", "agreement-sar", "agreement-ems"] if review else [
            "candidate", "stable-water"]
        reference_keys = ["ems-reference"] if not review and not without_ems else []
        map_layers = [places, layers["study-area"], *[layers[k] for k in reference_keys + primary], base]
        main = map_item(layout, 20, 53, 222, 207, map_layers, base.extent())
        main.setId("Main map")
        grid = main.grid()
        grid.setEnabled(True)
        grid.setIntervalX(5000)
        grid.setIntervalY(5000)
        grid.setStyle(QgsLayoutItemMapGrid.Cross)
        grid.setCrossLength(1.5)
        grid.setLineSymbol(QgsLineSymbol.createSimple({"line_color": "36,50,52,195", "line_width": ".2"}))
        grid.setAnnotationEnabled(True)
        grid.setAnnotationPrecision(0)
        grid.setAnnotationFont(QFont("Helvetica", 7))
        grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Right)
        grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Top)

        add_north_arrow(layout, main, 233, 264)
        bar = QgsLayoutItemScaleBar(layout)
        bar.setStyle("Single Box")
        bar.setLinkedMap(main)
        bar.setUnits(Qgis.DistanceUnit.Kilometers)
        bar.setUnitsPerSegment(2)
        bar.setNumberOfSegments(3)
        bar.setNumberOfSegmentsLeft(0)
        bar.setUnitLabel("km")
        bar.setFont(QFont("Helvetica", 8))
        layout.addLayoutItem(bar)
        bar.attemptMove(QgsLayoutPoint(20, 269))
        label(layout, f"1:{main.scale():,.0f} at A3  |  5 km UTM grid (m)",
              76, 267, 150, 8, 9, False, MUTED)
        label(layout, "WGS 84 / UTM zone 30N (EPSG:32630)  |  Arrow: grid north",
              76, 274, 151, 7, 8.5, False, MUTED)

        label(layout, "A   CATARROJA AND NORTHERN ALBUFERA", 260, 48, 142, 7, 10, True, MUTED)
        tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs, project)
        focus = tr.transformBoundingBox(QgsRectangle(-.445, 39.347, -.309, 39.409))
        inset = map_item(layout, 260, 56, 141, 70, map_layers, focus)
        inset.setId("Detail A")
        add_extent_frame(main, inset)
        label(layout, f"1:{inset.scale():,.0f} at A3  |  Black frame on main map shows detail A",
              260, 127, 141, 6, 8.5, False, MUTED)
        label(layout, "LOCATION  /  EASTERN SPAIN", 260, 136, 141, 6, 9, True, MUTED)
        locator_extent = tr.transformBoundingBox(QgsRectangle(-10, 35, 4, 44))
        map_item(layout, 260, 143, 141, 29, [locator, countries], locator_extent)
        label(layout, "Red dot: Valencia, Spain", 260, 173, 141, 6, 8.5, False, MUTED)
        label(layout, "LEGEND", 260, 182, 141, 6, 10, True, MUTED)
        add_legend(layout, main, 259, 188, 141, 27, primary + reference_keys + ["study-area"])
        label(layout, "OBSERVATIONS AND INTERPRETATION", 260, 222, 141, 6, 10, True, MUTED)
        details = [
            "Sentinel-1 RTC: 8 & 20 Oct (pre), 1 Nov (post), 2024",
            "Relative orbit 110  |  Analysis grid: 20 m",
            "Sentinel-2 RGB basemap: 26 Oct 2024 (pre-event)",
        ]
        if not without_ems:
            details.append("EMS AOI01 source image: 31 Oct 2024, 10:51 UTC")
        if review:
            change = metrics["metrics"]["change"]
            cell_km2 = metrics["resolution_m"] ** 2 / 1e6
            details.extend([
                f"Area (km²): both {change['tp'] * cell_km2:.2f}  |  SAR only {change['fp'] * cell_km2:.2f}"
                f"  |  EMS only {change['fn'] * cell_km2:.2f}",
                "Different dates: differences do not establish detection errors.",
            ])
        else:
            details.extend([
                f"SAR candidate area: {metrics['candidate_km2']:.2f} km²",
                "Candidate extent is not confirmed inundation.",
            ])
        label(layout, "\n".join(details), 260, 230, 141, 34, 9)
        label(layout, "Research map. Not for emergency or navigation use.",
              260, 269, 141, 8, 8.5, False, MUTED)
        sources = ("Data: Copernicus Sentinel-1 (Microsoft Planetary Computer); Sentinel-2 (Element 84); "
                   + ("" if without_ems else "Copernicus EMSR773; ") + "Natural Earth (locator).")
        label(layout, sources, 20, 282, 381, 6, 8.5, False, MUTED)
        label(layout, "Cartography: AI for Science course project team  |  21 Nov 2026  |  v2.1",
              20, 289, 381, 6, 8.5, False, MUTED)
        project.layoutManager().addLayout(layout)
        exporter = QgsLayoutExporter(layout)
        settings = QgsLayoutExporter.PdfExportSettings()
        settings.dpi = 250
        if exporter.exportToPdf(str(target / f"{name}.pdf"), settings) != QgsLayoutExporter.Success:
            raise RuntimeError("PDF export failed")
        image = QgsLayoutExporter.ImageExportSettings()
        image.dpi = 125
        if exporter.exportToImage(str(target / f"{name}.png"), image) != QgsLayoutExporter.Success:
            raise RuntimeError("PNG export failed")
        return main

    main_map = None
    if mode in ("both", "candidate"):
        main_map = sheet("01-water-change", False)
    if mode in ("both", "agreement"):
        review_map = sheet("02-source-agreement", True)
        if main_map is None:
            main_map = review_map
    for key, layer in layers.items():
        if key.startswith("agreement") or key == "ems-reference":
            project.layerTreeRoot().findLayer(layer.id()).setItemVisibilityChecked(False)
    project.viewSettings().setDefaultViewExtent(
        QgisReferencedRectangle(main_map.extent(), crs))
    path = target / "Valencia-2024.qgz"
    if not project.write(str(path)):
        raise RuntimeError("QGIS project write failed")
    reopened = QgsProject()
    if not reopened.read(str(path)) or not all(l.isValid() for l in reopened.mapLayers().values()):
        raise RuntimeError("QGIS project round-trip failed")
    for layout in reopened.layoutManager().layouts():
        main = layout.itemById("Main map")
        inset = layout.itemById("Detail A")
        arrow = layout.itemById("Grid north")
        if (inset.scale() >= main.scale() or main.overview().linkedMap() != inset
                or not main.grid().enabled() or not Path(arrow.picturePath()).is_file()):
            raise RuntimeError(f"Map reference elements failed to round-trip: {layout.name()}")
    if without_ems and any("EMS" in layer.name() or layer.name() == "agreement"
                           for layer in reopened.mapLayers().values()):
        raise RuntimeError("Reference layer leaked into SAR-only project")
    print(f"QGIS {Qgis.QGIS_VERSION}: {len(reopened.mapLayers())} layers, "
          f"{len(reopened.layoutManager().layouts())} layouts", flush=True)
    os._exit(0)


if __name__ == "__main__":
    from qgis.core import QgsReferencedRectangle as QgisReferencedRectangle
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "maps")
    parser.add_argument("--mode", choices=["both","candidate","agreement"], default="both")
    parser.add_argument("--without-ems", action="store_true")
    args = parser.parse_args()
    build(args.out.resolve(), args.mode, args.without_ems)
