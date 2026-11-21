"""Freeze historical OSM road geometry for the GIS agent experiment."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "inputs" / "assets"
BBOX = [-0.49, 39.34, -0.36, 39.45]
SNAPSHOT = "2024-10-28T23:59:59Z"
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
ROAD_CLASSES = [
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service",
]


def acquire():
    DEST.mkdir(parents=True, exist_ok=True)
    west, south, east, north = BBOX
    classes = "|".join(ROAD_CLASSES)
    query = (
        f'[out:json][timeout:120][date:"{SNAPSHOT}"];'
        f'way["highway"~"^({classes})$"]({south},{west},{north},{east});out meta geom;'
    )
    raw_path = DEST / "osm-roads-response.json"
    origin_path = DEST / "osm-request.json"
    if not raw_path.exists():
        failures = []
        for endpoint in ENDPOINTS:
            try:
                request = Request(
                    endpoint, data=urlencode({"data": query}).encode(),
                    headers={"User-Agent": "AI4Science-course-project/0.3"},
                )
                with urlopen(request, timeout=150) as response:
                    body = response.read()
                parsed = json.loads(body)
                if parsed.get("remark") or not parsed.get("elements"):
                    raise ValueError(f"Incomplete or empty Overpass response: {parsed.get('remark')}")
                raw_path.write_bytes(body)
                origin_path.write_text(json.dumps({
                    "endpoint": endpoint, "query": query,
                    "retrieved_utc": datetime.now(timezone.utc).isoformat(),
                    "failures_before_success": failures,
                }, indent=2))
                break
            except Exception as exc:
                failures.append({"endpoint": endpoint, "error": str(exc)})
                print(f"Endpoint failed: {endpoint}: {exc}", flush=True)
        else:
            (DEST / "acquisition-failure.json").write_text(json.dumps(failures, indent=2))
            raise RuntimeError("All historical OSM endpoints failed; no current-data substitution")
    parsed = json.loads(raw_path.read_text())
    features = []
    seen = set()
    for element in parsed["elements"]:
        if element["type"] != "way" or element["id"] in seen:
            raise ValueError("Unexpected element type or duplicate way ID")
        seen.add(element["id"])
        coords = [[p["lon"], p["lat"]] for p in element["geometry"]]
        if len(coords) < 2:
            raise ValueError(f"Missing geometry for {element['id']}")
        tags = element.get("tags", {})
        features.append({
            "type": "Feature",
            "properties": {
                "osm_id": str(element["id"]), "highway": tags.get("highway"),
                "name": tags.get("name", ""), "osm_version": element["version"],
                "edited_utc": element["timestamp"],
                "bridge": tags.get("bridge", ""), "tunnel": tags.get("tunnel", ""),
            },
            "geometry": {"type": "LineString", "coordinates": coords},
        })
    roads = DEST / "roads-20241028.geojson"
    roads.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False))
    ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
    (DEST / "study-area.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"area_id": "HortaSud"},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}}],
    }))
    origin = json.loads(origin_path.read_text())
    provenance = {
        **origin, "snapshot_utc": SNAPSHOT, "bbox_lon_lat": BBOX,
        "license": "ODbL 1.0", "attribution": "OpenStreetMap contributors",
        "license_url": "https://www.openstreetmap.org/copyright",
        "response_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "roads_sha256": hashlib.sha256(roads.read_bytes()).hexdigest(),
        "features": len(features), "selected_highway_classes": ROAD_CLASSES,
        "coverage": "OSM mapped road ways intersecting bbox; clip to AOI before measurement.",
        "limits": [
            "Historical OSM snapshot is not a complete or surveyed road inventory.",
            "Planar overlap with flood extent measures exposure, not damage or closure.",
            "Bridge and tunnel elevation is not resolved by two-dimensional overlay.",
            "Separate carriageways remain separate unless their geometries coincide.",
        ],
    }
    (DEST / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2))
    print(json.dumps({"roads": len(features), "snapshot": SNAPSHOT, "path": str(roads)}))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    acquire()
