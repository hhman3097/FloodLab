"""Independent Shapely reference calculation; never supplied to the agent."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import shape
from shapely.ops import transform, unary_union

ROOT = Path(__file__).resolve().parent
CRS = "EPSG:32630"
ABS_TOLERANCE_M = 0.01
REL_TOLERANCE = 1e-7


def read_features(path):
    path = Path(path)
    if path.suffix == ".geojson":
        return json.loads(path.read_text())["features"]
    ogr = ROOT.parent / ".tools/qgis/bin/ogr2ogr"
    content = subprocess.check_output([str(ogr), "-f", "GeoJSON", "/vsistdout/", str(path)])
    return json.loads(content)["features"]


def verify(path, total_field="total_m", exposed_field="exposed_m", ratio_field="ratio",
           grid_size=1000, classes=None, ratio_scale=1):
    project = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform
    area = transform(project, shape(read_features(ROOT / "inputs/assets/study-area.geojson")[0]["geometry"]))
    source = read_features(ROOT / "inputs/assets/roads-20241028.geojson")
    ids = [f["properties"]["osm_id"] for f in source]
    assert len(ids) == len(set(ids)), "Duplicate OSM way IDs"
    roads = unary_union([
        transform(project, shape(f["geometry"])) for f in source
        if classes is None or f["properties"]["highway"] in classes
    ]).intersection(area)
    flood = unary_union([make_valid(shape(f["geometry"]))
                         for f in read_features(ROOT / "products/ems-reference.geojson")])
    flooded = roads.intersection(flood)
    features = read_features(path)
    cells = [shape(f["geometry"]) for f in features]
    mismatches = []
    max_abs_error = 0.0
    numeric_rows = []
    for feature, cell in zip(features, cells):
        expected_total = roads.intersection(cell).length
        expected_exposed = flooded.intersection(cell).length
        expected_ratio = expected_exposed / expected_total if expected_total > 0 else None
        props = feature["properties"]
        for field, expected in ((total_field, expected_total), (exposed_field, expected_exposed),
                                (ratio_field, expected_ratio)):
            actual = props.get(field)
            if field == ratio_field and isinstance(actual, (float, int)):
                actual = actual / ratio_scale
            if expected is None:
                good = actual is None
            else:
                tol = ABS_TOLERANCE_M if field != ratio_field else 1e-7
                good = isinstance(actual, (int, float)) and np.isclose(
                    actual, expected, atol=tol, rtol=REL_TOLERANCE)
                if isinstance(actual, (int, float)) and field != ratio_field:
                    max_abs_error = max(max_abs_error, abs(actual - expected))
            if not good:
                mismatches.append({"id": props.get("id"), "field": field,
                                   "actual": actual, "expected": expected})
        numeric_rows.append({"id": props.get("id"), "total_m": expected_total,
                             "exposed_m": expected_exposed, "ratio": expected_ratio})
    xmin, ymin, xmax, ymax = area.bounds
    expected_count = int(np.ceil((xmax-xmin)/grid_size) * np.ceil((ymax-ymin)/grid_size))
    covered = unary_union(cells)
    checks = {
        "numeric": not mismatches,
        "grid_count": len(cells) == expected_count,
        "grid_width_height": all(abs((c.bounds[2]-c.bounds[0])-grid_size) < 1e-5 and
                                abs((c.bounds[3]-c.bounds[1])-grid_size) < 1e-5 for c in cells),
        "grid_covers_aoi": area.difference(covered).area < 0.001,
        "grid_nonoverlap": abs(sum(c.area for c in cells)-covered.area) < 0.001,
    }
    return {
        "layer_file": Path(path).name,
        "layer_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "fields": {"total": total_field, "exposed": exposed_field, "ratio": ratio_field,
                   "ratio_scale": ratio_scale},
        "passed": all(checks.values()), "checks": checks, "cells": len(cells),
        "classes": classes, "grid_size_m": grid_size,
        "expected_total_m": roads.length, "expected_exposed_m": flooded.length,
        "max_absolute_length_error_m": max_abs_error, "mismatches": mismatches[:30],
        "reference_top5": sorted(numeric_rows, key=lambda r: r["exposed_m"], reverse=True)[:5],
        "reference_ratio_top5": sorted(
            [r for r in numeric_rows if r["ratio"] is not None],
            key=lambda r: r["ratio"], reverse=True)[:5],
        "tolerance": {"absolute_m": ABS_TOLERANCE_M, "relative": REL_TOLERANCE},
        "independence": "Separate Shapely overlay code and original source layers. Both stacks use GEOS; not independent survey truth.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--total-field", default="total_m")
    parser.add_argument("--exposed-field", default="exposed_m")
    parser.add_argument("--ratio-field", default="ratio")
    parser.add_argument("--grid-size", type=float, default=1000)
    parser.add_argument("--classes", nargs="+")
    parser.add_argument("--ratio-scale", type=float, default=1)
    args = parser.parse_args()
    result = verify(args.path, args.total_field, args.exposed_field, args.ratio_field,
                    args.grid_size, args.classes, args.ratio_scale)
    content = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(content)
    print(content)
    raise SystemExit(0 if result["passed"] else 1)
