"""Verify a published radar project using downloaded rasters, mask, and vectors."""
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile

import numpy as np
from osgeo import gdal
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(folder):
    report = read(folder / "results.json")
    source = folder.parents[1] / "inputs" / report["input"]
    captures = read(source / "download.json")["captures"]
    valid_source = None
    for entry in captures:
        path = source / entry["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        image = gdal.Open(str(path))
        assert image is not None and image.RasterCount == 2
        valid = np.isfinite(image.ReadAsArray()).all(axis=0)
        valid_source = valid if valid_source is None else valid_source & valid
    mask = gdal.Open(str(folder / "candidate.tif"))
    values = mask.ReadAsArray()
    assert set(np.unique(values)).issubset({0, 1, 255})
    assert np.array_equal(valid_source, values != 255)
    count = int((values == 1).sum())
    pixel_area_m2 = count * abs(mask.GetGeoTransform()[1] * mask.GetGeoTransform()[5])
    assert count == report["candidate_pixels"]
    assert abs(pixel_area_m2 - 1e6 * report["candidate_km2"]) < 0.05
    geom = QgsVectorLayer(str(folder / "candidate.gpkg") + "|layername=candidate", "candidate", "ogr")
    assert geom.isValid() and geom.featureCount() == report["patches"]
    vector_area_m2 = 0.0
    for f in geom.getFeatures():
        g = f.geometry()
        assert g.isGeosValid()
        vector_area_m2 += g.area()
    assert abs(pixel_area_m2 - vector_area_m2) < 0.05
    with ZipFile(folder / "project.zip") as package:
        assert package.testzip() is None
        assert all("inputs/" + report["input"] + "/" + e["file"] in package.namelist() for e in captures)
    project = QgsProject()
    assert project.read(str(folder / "map.qgz"))
    assert len(project.mapLayers()) >= 3 and all(layer.isValid() for layer in project.mapLayers().values())
    assert project.layoutManager().layouts()
    project.clear()
    agreement = gdal.Open(str(folder / "agreement.tif"))
    a = agreement.ReadAsArray()
    comp = report["reference_comparison"]
    assert np.count_nonzero(a != 255) == comp["common_valid"]
    assert all(np.count_nonzero(a == code) == comp[name]
               for code, name in enumerate(("both", "candidate_only", "reference_only"), 1))
    return {"candidate_pixels": count, "valid_pixels": int(valid_source.sum()), "pixel_area_m2": pixel_area_m2,
            "vector_area_m2": vector_area_m2, "patches": geom.featureCount(),
            "agreement_raster_count_verified": True, "qgis_project_reopened": True, "passed": True}


if __name__ == "__main__":
    gdal.UseExceptions()
    app = QgsApplication([], False)
    app.initQgis()
    root = Path(sys.argv[1])
    value = verify(root)
    payload = json.dumps(value, indent=2) + "\n"
    print(payload, end="")
    (root / "verification.json").write_text(payload)
