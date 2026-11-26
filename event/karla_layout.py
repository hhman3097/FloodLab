"""Create a portable, editable QGIS map for each independently saved Karla output."""
import json
import shutil
import sys
from pathlib import Path

from osgeo import gdal
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsLayoutExporter, QgsLayoutItemLabel, QgsLayoutItemMap, QgsLayoutItemMapGrid, QgsLayoutItemPage,
    QgsLayoutItemPicture, QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize, QgsPalettedRasterRenderer,
    QgsLineSymbol, QgsPrintLayout, QgsProject, QgsRasterLayer, QgsRectangle, QgsVectorLayer,
    QgsCategorizedSymbolRenderer, QgsRendererCategory)

COLORS = {1:"#157c6d", 2:"#c56b24", 3:"#65518e", 255:"#a6acb1"}
CLASSES = {1:"Both observations",2:"SAR only",3:"Optical only",255:"Not comparable"}


def render(folder, raster_path, scope):
    result = json.loads((folder / "results.json").read_text(encoding="utf-8"))
    kind = "exposure" if "road_m" in result else "segment" if "algorithm" in result else "comparison"
    title = {"exposure":"Karla Lake | Cropland and road screening",
             "segment":"Karla Lake | Flood recovery observation",
             "comparison":"Karla Lake | Radar and optical evidence"}[kind]
    project = QgsProject.instance()
    crs = QgsCoordinateReferenceSystem("EPSG:32634")
    project.setCrs(crs)
    project.setFilePathStorage(Qgis.FilePathType.Relative)
    legend = CLASSES if kind != "segment" else {1:"Candidate inundation", 2:"Pre-existing water proxy", 255:"Uninterpretable"}
    palette = COLORS if kind != "segment" else {1:"#187f8e",2:"#a1d9e0",255:"#a6acb1"}
    original=gdal.Open(str(raster_path))
    array=original.ReadAsArray()
    from sar_analysis import raster_like
    display=raster_like(original, array,folder/"map-classes.tif")
    display.FlushCache()
    display=None
    layer = QgsRasterLayer(str(folder/"map-classes.tif"), "Flood evidence classes")
    if not layer.isValid():
        raise ValueError("Evidence raster unavailable")
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(),1,[QgsPalettedRasterRenderer.Class(
        0,QColor("#f4f5f4"),"Interpretable, no candidate"), *[QgsPalettedRasterRenderer.Class(
        c,QColor(color),legend[c]) for c,color in palette.items()]]))
    project.addMapLayer(layer)
    context = []
    if kind == "exposure":
        roads = QgsVectorLayer(str(folder / "roads.geojson"), "Road verification segments", "ogr")
        if roads.isValid() and roads.featureCount():
            shades={"both":"#0a302c","sar_only":"#8d4112","optical_only":"#3c265e","unknown":"#626a70"}
            roads.setRenderer(QgsCategorizedSymbolRenderer("evidence", [QgsRendererCategory(
                label,QgsLineSymbol.createSimple({"line_color":color,"line_width":".77"}),label)
                for label,color in shades.items()]))
            project.addMapLayer(roads)
            context.append(roads)
        farm = QgsRasterLayer(str(folder / "landcover.tif"), "ESA WorldCover 2021 source classes")
        if farm.isValid():
            farm.setRenderer(QgsPalettedRasterRenderer(farm.dataProvider(),1,[
                QgsPalettedRasterRenderer.Class(40,QColor("#d7a85b"),"WorldCover cropland")]))
            project.addMapLayer(farm)
            project.layerTreeRoot().findLayer(farm.id()).setItemVisibilityChecked(False)
    if kind != "exposure" and (folder / "candidates.gpkg").is_file():
        candidate = QgsVectorLayer(str(folder / "candidates.gpkg") + "|layername=candidates", "Candidate polygons", "ogr")
        project.addMapLayer(candidate)
        project.layerTreeRoot().findLayer(candidate.id()).setItemVisibilityChecked(False)
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("Karla flood evidence")
    layout.pageCollection().page(0).setPageSize("A3", QgsLayoutItemPage.Landscape)
    layout.renderContext().setDpi(160)

    def text(value,x,y,w,h,size=11,bold=False):
        l=QgsLayoutItemLabel(layout)
        font=QFont("Helvetica",size)
        font.setBold(bold)
        l.setFont(font)
        l.setFontColor(QColor("#192c34"))
        l.setText(value)
        layout.addLayoutItem(l)
        l.attemptMove(QgsLayoutPoint(x,y))
        l.attemptResize(QgsLayoutSize(w,h))
        return l

    text(title,16,7,390,13,24,True)
    dates=" and ".join(result.get("dates", ["2023-12-18","2023-12-19"]))
    text("THESSALY, GREECE  /  " + dates + "  /  20 m UTM GRID",16,23,390,8,11)
    item=QgsLayoutItemMap(layout)
    layout.addLayoutItem(item)
    item.attemptMove(QgsLayoutPoint(29,43))
    item.attemptResize(QgsLayoutSize(266,212))
    item.setCrs(crs)
    item.setLayers([*context,layer])
    item.setKeepLayerSet(True)
    item.setFrameEnabled(True)
    item.setBackgroundColor(QColor("#ffffff"))
    rect=QgsRectangle(layer.extent())
    # The source raster's extent is stable. The focus output is a separate scope.
    if scope=="north":
        from karla_data import FOCUS
        x=QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),crs,project)
        rect=x.transformBoundingBox(QgsRectangle(*FOCUS))
    item.zoomToExtent(rect)
    grid=item.grid()
    grid.setEnabled(True)
    grid.setIntervalX(5000)
    grid.setIntervalY(5000)
    grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)
    grid.setFrameStyle(QgsLayoutItemMapGrid.ExteriorTicks)
    grid.setAnnotationEnabled(True)
    grid.setAnnotationPrecision(0)
    grid.setAnnotationFont(QFont("Helvetica",8))
    grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll,QgsLayoutItemMapGrid.Right)
    text("EVIDENCE KEY",310,44,99,8,12,True)
    for i,(value,label) in enumerate([*legend.items(), (0,"No candidate in valid pixels")]):
        y=57+9*i
        sample=text("",310,y+2,11,4)
        sample.setBackgroundEnabled(True)
        sample.setBackgroundColor(QColor(palette.get(value,"#f4f5f4")))
        text(label,326,y,80,8)
    text("RESULTS IN THE MAPPED WINDOW",310,105,103,9,11,True)
    if kind=="segment":
        info=f"{result['candidate_km2']:.2f} km2 candidate area\n{result['valid_pct']:.1f}% interpretable pre/post\nMethod: {result['algorithm'].replace('_',' ')}"
    elif kind=="comparison":
        info=f"{result['both_km2']:.2f} km2 in both observations\n{result['sar_only_km2']:.2f} km2 SAR only\n{result['optical_only_km2']:.2f} km2 optical only\n{result['unknown_km2']:.2f} km2 not comparable"
    else:
        f,r=result["cropland_km2"],result["road_m"]
        info=f"Scope: {scope}\nCropland with both: {f['both']:.2f} km2\nRoads with both: {r['both']/1000:.2f} km\nRoads outside comparison: {r['unknown']/1000:.2f} km"
    text(info,310,116,101,39,11)
    text("SOURCES AND LIMITS",310,168,99,8,11,True)
    text("Copernicus Sentinel-1 RTC / Sentinel-2 L2A\nMicrosoft Planetary Computer\n"
         + ("ESA WorldCover 2021 / OSM 2026 snapshot\n" if kind=="exposure" else "") +
         "WGS 84 / UTM 34N (EPSG:32634)",310,179,102,30,9)
    text("Evidence categories are not flood dynamics.\nRoad overlap does not prove closure.",310,218,101,18,10)
    scale=QgsLayoutItemScaleBar(layout)
    scale.setStyle("Single Box")
    scale.setLinkedMap(item)
    scale.setUnits(Qgis.DistanceUnit.Kilometers)
    scale.setUnitsPerSegment(2)
    scale.setNumberOfSegments(2)
    scale.setNumberOfSegmentsLeft(0)
    scale.setUnitLabel("km")
    layout.addLayoutItem(scale)
    scale.attemptMove(QgsLayoutPoint(25,267))
    symbol=folder/"north.svg"
    shutil.copyfile(Path(__file__).resolve().parent/"cartography_data/north.svg",symbol)
    north=QgsLayoutItemPicture(layout)
    layout.addLayoutItem(north)
    north.attemptMove(QgsLayoutPoint(279,263))
    north.attemptResize(QgsLayoutSize(11,19))
    north.setPicturePath(str(symbol))
    text("N (grid)",259,266,19,8,9)
    project.layoutManager().addLayout(layout)
    if not project.write(str(folder/"map.qgz")):
        raise RuntimeError("Cannot write QGIS project")
    exporter=QgsLayoutExporter(layout)
    image=QgsLayoutExporter.ImageExportSettings()
    image.dpi=125
    if exporter.exportToPdf(str(folder/"map.pdf"),QgsLayoutExporter.PdfExportSettings())!=QgsLayoutExporter.Success or \
       exporter.exportToImage(str(folder/"map.png"),image)!=QgsLayoutExporter.Success:
        raise RuntimeError("Cannot export map")
    project.clear()


if __name__=="__main__":
    qgs=QgsApplication([],False)
    qgs.initQgis()
    render(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3])
