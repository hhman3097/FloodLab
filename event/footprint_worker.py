"""Equal-area footprint union, grid intersections and editable QGIS map."""
import json
import math
import os
import sys
from pathlib import Path
from runtime import LIBRARY
_dll=os.add_dll_directory(str(LIBRARY/"bin")) if os.name=="nt" else None
from qgis.core import (QgsApplication,QgsProject,QgsCoordinateReferenceSystem,QgsCoordinateTransform,
    QgsGeometry,QgsRectangle,QgsVectorLayer,QgsPrintLayout,QgsLayoutItemMap,QgsLayoutItemLabel,
    QgsLayoutItemScaleBar,QgsLayoutPoint,QgsLayoutSize,QgsLayoutExporter,QgsFillSymbol,
    QgsPalLayerSettings,QgsTextFormat,QgsTextBufferSettings,QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtGui import QColor


def main():
    source,out=Path(sys.argv[1]),Path(sys.argv[2]);step=int(sys.argv[3])
    p=json.loads((source.parent/"provenance.json").read_text(encoding="utf-8"))
    app=QgsApplication([],False);app.initQgis()
    project=QgsProject.instance();src=QgsVectorLayer(str(source),"Observed flood / traces","ogr")
    if not src.isValid() or not src.crs().isValid():raise ValueError("源图层或坐标系无效")
    equal=QgsCoordinateReferenceSystem("EPSG:6933")
    tr=QgsCoordinateTransform(src.crs(),equal,project)
    shapes=[]
    for f in src.getFeatures():
        g=f.geometry();g.transform(tr);shapes.append(g if g.isGeosValid() else g.makeValid())
    union=QgsGeometry.unaryUnion(shapes);area=union.area();extent=union.boundingBox()
    if not math.isfinite(area) or area<=0:raise ValueError("无有效的观测面积")
    x0=math.floor(extent.xMinimum()/step)*step;y0=math.floor(extent.yMinimum()/step)*step
    nx=math.ceil((extent.xMaximum()-x0)/step);ny=math.ceil((extent.yMaximum()-y0)/step)
    if nx*ny>16000:raise ValueError("网格过密，请选择更大的网格")
    back=QgsCoordinateTransform(equal,QgsCoordinateReferenceSystem("EPSG:4326"),project)
    features=[];rows=[]
    for j in range(ny):
        for i in range(nx):
            x=x0+i*step;y=y0+j*step
            g=QgsGeometry.fromRect(QgsRectangle(x,y,x+step,y+step))
            overlap=union.intersection(g).area()
            if overlap<=0:continue
            record={"cell":f"{i+1}-{j+1}","flood_km2":overlap/1e6,"cell_pct":100*overlap/step**2}
            g.transform(back)
            rows.append(record);features.append({"type":"Feature","properties":record,"geometry":json.loads(g.asJson())})
    tol=max(1,area*1e-6)
    if abs(sum(r["flood_km2"] for r in rows)*1e6-area)>tol:raise ValueError("分格面积与合并面积不符")
    ordered=sorted(rows,key=lambda r:(-r["flood_km2"],r["cell"]))
    for rank,row in enumerate(ordered,1):
        row["rank"]=rank
    (out/"grid.geojson").write_text(json.dumps({"type":"FeatureCollection","features":features}),encoding="utf-8")
    result={"total_km2":area/1e6,"grid_m":step,"occupied_cells":len(rows),"top_cells":ordered[:5],
            "measurement_crs":"EPSG:6933","features":len(shapes),"notation":p["notation"],"source":p["source"],
            "aoi":p["aoi"],"observations":p["observations"],"input_sha256":p["sha256"]}
    (out/"results.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    project.setCrs(equal)
    grid=QgsVectorLayer(str(out/"grid.geojson"),"Occupied cells / area ranking","ogr")
    if not grid.isValid():raise ValueError("网格图层无法打开")
    grid.renderer().setSymbol(QgsFillSymbol.createSimple({
        "color":"0,0,0,0","outline_color":"125,142,153,255","outline_width":"0.23"}))
    labels=QgsPalLayerSettings();labels.isExpression=True
    labels.fieldName='CASE WHEN "rank" <= 5 THEN to_string("rank") || \' / \' || "cell" END'
    text_format=QgsTextFormat();text_format.setSize(9);text_format.setColor(QColor("#19344c"))
    halo=QgsTextBufferSettings();halo.setEnabled(True);halo.setSize(0.7);halo.setColor(QColor("white"))
    text_format.setBuffer(halo);labels.setFormat(text_format)
    grid.setLabeling(QgsVectorLayerSimpleLabeling(labels));grid.setLabelsEnabled(True)
    project.addMapLayer(grid);project.addMapLayer(src);src.renderer().symbol().setColor(QColor("#177aab"))
    project.setFileName(str(out/"map.qgz"))
    layout=QgsPrintLayout(project);layout.initializeDefaults();layout.setName("Observed footprint")
    layout.pageCollection().pages()[0].setPageSize(QgsLayoutSize(297,210))
    def place(item,x,y,w,h):
        layout.addLayoutItem(item);item.attemptMove(QgsLayoutPoint(x,y));item.attemptResize(QgsLayoutSize(w,h))
    def label(text,x,y,w,h):
        item=QgsLayoutItemLabel(layout);item.setText(text);place(item,x,y,w,h)
    label(f"{p['code']} | {p['aoi']} | Observed flood footprint",10,4,256,10)
    m=QgsLayoutItemMap(layout);place(m,10,18,277,156);m.setFrameEnabled(True);m.setCrs(equal);m.setLayers([src,grid]);m.setKeepLayerSet(True)
    mapped=QgsRectangle(x0,y0,x0+nx*step,y0+ny*step);mapped.scale(1.06);m.zoomToExtent(mapped)
    scale=QgsLayoutItemScaleBar(layout);scale.setStyle("Single Box");scale.setLinkedMap(m)
    from qgis.core import QgsUnitTypes
    scale.setUnits(QgsUnitTypes.DistanceKilometers);scale.setUnitLabel("km");scale.setNumberOfSegments(2);scale.setNumberOfSegmentsLeft(0)
    target=m.extent().width()/10_000
    magnitude=10**math.floor(math.log10(target))
    scale.setUnitsPerSegment(max(v*magnitude for v in (1,2,5) if v*magnitude<=target));place(scale,12,176,45,8)
    label("N ^",271,5,18,9)
    label(f"Blue: observed flood / traces ({area/1e6:.3f} km2) | Gray: intersecting {step} m cells | Labels: rank / cell",10,191,277,8)
    label("Copernicus EMS | EPSG:6933 | "+", ".join(p["observations"])+" | Mapped coverage only",10,200,280,8)
    project.layoutManager().addLayout(layout)
    if not project.write():raise RuntimeError("无法保存 QGIS 工程")
    exporter=QgsLayoutExporter(layout)
    from qgis.core import Qgis
    settings=QgsLayoutExporter.PdfExportSettings()
    settings.textRenderFormat=Qgis.TextRenderFormat.AlwaysText
    if exporter.exportToPdf(str(out/"map.pdf"),settings)!=QgsLayoutExporter.Success:raise RuntimeError("无法导出 PDF")
    if exporter.exportToImage(str(out/"map.png"),QgsLayoutExporter.ImageExportSettings())!=QgsLayoutExporter.Success:raise RuntimeError("无法导出 PNG")
    sys.stdout.flush();os._exit(0)


if __name__=="__main__":main()
