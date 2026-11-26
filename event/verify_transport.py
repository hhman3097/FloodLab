"""Independently check the transport report's source-to-table lengths with PyQGIS."""
import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

from qgis.core import (QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsGeometry, QgsProject, QgsVectorLayer, QgsWkbTypes)

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "inputs/assets"


def loaded(path):
    return json.loads(path.read_text(encoding="utf-8"))


def projected(path, *fields):
    layer = QgsVectorLayer(str(path), path.stem, "ogr")
    if not layer.isValid():
        raise ValueError(f"Cannot read {path}")
    tx = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:32630"), QgsProject.instance())
    for f in layer.getFeatures():
        g = f.geometry()
        g.transform(tx)
        if g.type() == QgsWkbTypes.PolygonGeometry and not g.isGeosValid():
            g = g.makeValid()
        yield [str(f[field]) for field in fields], g


def union(geometries):
    items = [g for g in geometries if not (g.isNull() or g.isEmpty())]
    return QgsGeometry.unaryUnion(items) if items else QgsGeometry()


def length(geometry):
    return 0.0 if geometry.isNull() or geometry.isEmpty() else geometry.length()


def intersect_length(line, area):
    return 0.0 if line.isEmpty() or area.isEmpty() else length(line.intersection(area))


def validate(run, towns, observed):
    result = loaded(run / "analysis.json")
    art = QgsProject()
    assert art.read(str(run / "transport.qgz"))
    assert len(art.mapLayers()) >= 5 and all(l.isValid() for l in art.mapLayers().values())
    assert art.layoutManager().layouts()
    art.clear()
    selected = set(result["scope_codes"])
    activation = next(x for x in loaded(ROOT / "inputs/activation.json")["results"] if x["code"] == "EMSR773")
    prod = next(p for a in activation["aois"] for p in a["products"] if p["id"] == result["sources"]["product_id"])
    source_extent = QgsGeometry.fromWkt(prod["extent"])
    wgs_to_utm = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                       QgsCoordinateReferenceSystem("EPSG:32630"), QgsProject.instance())
    source_extent.transform(wgs_to_utm)
    aoi = next(projected(ASSETS / "study-area.geojson"))[1]
    coverage = source_extent.intersection(aoi)
    groups = defaultdict(list)
    for (label,), geom in projected(observed, "notation"):
        if geom.boundingBox().intersects(coverage.boundingBox()):
            groups[label].append(geom)
    wet = union(groups["Flooded area"]).intersection(coverage)
    trace = union(groups["Flood trace"]).intersection(coverage)
    observed_union = union([wet, trace])
    roads = union(g for (h,), g in projected(ASSETS / "roads-20241028.geojson", "highway")
                  if h in result["road_classes"])
    independent = {}
    for (code,), town in sorted(projected(towns, "cod_ine_mun"), key=lambda x: x[0][0]):
        if code not in selected:
            continue
        scope = coverage.intersection(town)
        centerlines = roads.intersection(scope) if not scope.isEmpty() else QgsGeometry()
        direct = intersect_length(centerlines, wet)
        combined = intersect_length(centerlines, observed_union)
        independent[code] = {"assessed_m": length(centerlines), "flooded_area_m": direct,
                             "flood_trace_m": combined - direct, "exposed_m": combined,
                             "coverage_pct": 100 * scope.area() / town.area()}
    results_by_town = {v["town_code"]: v for v in result["municipalities"]}
    assert selected == independent.keys() == results_by_town.keys()
    errors = defaultdict(float)
    sums = defaultdict(float)
    csv_rows = {}
    with (run / "segments.csv").open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            assert r["segment_id"] not in csv_rows
            csv_rows[r["segment_id"]] = r
            sums[r["town_code"], r["evidence"]] += float(r["overlap_m"])
    for (uid, code, evidence, metres), g in projected(run / "segments.geojson", "segment_id", "town_code", "evidence", "overlap_m"):
        r = csv_rows.pop(uid)
        assert r["town_code"] == code and r["evidence"] == evidence and code in selected
        errors["segment_geometry_m"] = max(errors["segment_geometry_m"], abs(float(metres) - g.length()))
        errors["segment_csv_m"] = max(errors["segment_csv_m"], abs(float(metres) - float(r["overlap_m"])))
    assert not csv_rows
    for code, value in independent.items():
        for k, raw in value.items():
            actual = results_by_town[code][k]
            assert math.isfinite(raw) and math.isfinite(actual)
            errors[k] = max(errors[k], abs(raw - actual))
        for kind in ("flooded_area", "flood_trace"):
            key = kind + "_m"
            errors["segment_sum_m"] = max(errors["segment_sum_m"], abs(results_by_town[code][key] - sums[code, kind]))
    thresholds = {k: (.05 if k != "coverage_pct" else .001) for k in errors}
    return {"analysis_id": result["analysis_id"], "segments": result["segments"],
            "municipalities": len(independent), "qgis_project_reopened": True,
            "independent_method": "PyQGIS reproject and intersect raw sources; shared GEOS, not survey truth",
            "max_errors": dict(errors), "thresholds": thresholds,
            "passed": all(errors[k] <= tol for k, tol in thresholds.items())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--municipalities", required=True, type=Path)
    parser.add_argument("--observed", required=True, type=Path)
    args = parser.parse_args()
    qgis = QgsApplication([], False)
    qgis.initQgis()
    report = validate(args.run, args.municipalities, args.observed)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    (args.run / "verification.json").write_text(payload, encoding="utf-8")
    print(payload, end="")
    raise SystemExit(not report["passed"])
