"""Verify Karla Lake artifacts directly from masks, road geometries and project files."""
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from osgeo import gdal
from pyproj import Transformer
from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsVectorLayer)

from karla_data import sha


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(root):
    state = read(root / "data-index.json")
    entries = {key:root / row["path"] for key,row in state["karla"].items()}
    outputs = state["outputs"]
    assert len(outputs) == 5
    verified = {}
    for item in outputs:
        path = root / item["folder"]
        results = read(path / "results.json")
        with ZipFile(path / "project.zip") as archive:
            assert archive.testzip() is None
        project = QgsProject()
        assert project.read(str(path / "map.qgz"))
        assert project.layoutManager().layouts()
        assert all(layer.isValid() for layer in project.mapLayers().values())
        project.clear()
        if "algorithm" in results:
            src = entries[results["input_id"]]
            for image in read(src / "download.json")["captures"]:
                assert sha(src/image["file"]) == image["sha256"]
            raster = gdal.Open(str(path/"evidence.tif")).ReadAsArray()
            vector = QgsVectorLayer(str(path/"candidates.gpkg")+"|layername=candidates","candidate","ogr")
            assert vector.isValid()
            area = sum(f.geometry().area() for f in vector.getFeatures())
            assert abs(area - np.count_nonzero(raster==1)*400) < .05
            assert math.isclose(area/1e6,results["candidate_km2"],abs_tol=1e-9)
            assert (raster != 255).sum() == results["valid_pixels"]
            verified[item["key"]]={"area_km2":area/1e6,"raster_vector_agree":True}
        elif "both_km2" in results:
            ds = gdal.Open(str(path/"agreement.tif"))
            values = ds.ReadAsArray()
            a,b=[gdal.Open(str(entries[results[key]]/"evidence.tif")).ReadAsArray()
                 for key in ("sar_id","optical_id")]
            joint=(a!=255)&(b!=255)
            for mask,code in ((~joint,255), (joint&(a==1)&(b==1),1),
                              (joint&(a==1)&(b!=1),2),(joint&(b==1)&(a!=1),3)):
                assert np.array_equal(values==code, mask)
            verified[item["key"]]={"compared_pixels":int(joint.sum()),"classes_verified":True}
        else:
            lines=QgsVectorLayer(str(path/"roads.geojson"),"roads","ogr")
            assert lines.isValid()
            tx=QgsCoordinateTransform(lines.crs(),QgsCoordinateReferenceSystem("EPSG:32634"),QgsProject.instance())
            totals=defaultdict(float)
            for f in lines.getFeatures():
                g=f.geometry()
                g.transform(tx)
                totals[f["evidence"]]+=g.length()
            with (path/"roads.csv").open(encoding="utf-8-sig") as f:
                assert len(list(csv.DictReader(f))) == lines.featureCount() == results["segments"]
            assert all(abs(totals[name]-value)<.05 for name,value in results["road_m"].items())
            assert sha(path/"roads-source.json")
            ds=gdal.Open(str(entries[results["input_comparison_id"]]/"agreement.tif"))
            land=gdal.Open(str(path/"landcover.tif")).ReadAsArray()==40
            gt=ds.GetGeoTransform()
            yy,xx=np.indices(land.shape)
            lon,lat=Transformer.from_crs(32634,4326,always_xy=True).transform(
                gt[0]+(xx+.5)*gt[1],gt[3]+(yy+.5)*gt[5])
            w,s,e,n=results["bbox"]
            land&=(lon>=w)&(lon<=e)&(lat>=s)&(lat<=n)
            codes=ds.ReadAsArray()
            assert abs(land.sum()*.0004-results["cropland_total_km2"])<1e-8
            for code,key in ((1,"both"),(2,"sar_only"),(3,"optical_only"),(255,"unknown")):
                assert abs(np.count_nonzero(land&(codes==code))*.0004-results["cropland_km2"][key])<1e-8
            verified[item["key"]]={"road_features":lines.featureCount(),"road_and_cropland_verified":True}
    return {"passed":True,"all_qgis_projects_reopened":True,"outputs":verified}


if __name__=="__main__":
    gdal.UseExceptions()
    app=QgsApplication([],False)
    app.initQgis()
    root=Path(sys.argv[1])
    content=json.dumps(verify(root),ensure_ascii=False,indent=2)+"\n"
    (root/"verification.json").write_text(content,encoding="utf-8")
    print(content,end="")
