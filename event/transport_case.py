"""Deterministic Horta Sud transport tools for the Pi workspace."""
import csv
import gzip
import hashlib
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely import from_wkt, make_valid
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union

from runtime import environment, executable

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "inputs/assets"
ARCHIVE = BASE / "transport"
CLASSES = tuple(f"{r}{s}" for r in ("motorway", "trunk", "primary", "secondary") for s in ("", "_link"))
LOCAL_CLASSES = ("tertiary", "tertiary_link", "residential", "unclassified")
PROD = "EMSR773/AOI03/GRA/v2"
ICV = "https://terramapas.icv.gva.es/0105_Delimitaciones?outputformat=gpkg&request=GetFeature&service=WFS&typename=ICV.Municipios&version=2.0.0"
METRIC = "EPSG:32630"
TO_METRIC = Transformer.from_crs("EPSG:4326", METRIC, always_xy=True).transform
TO_LONLAT = Transformer.from_crs(METRIC, "EPSG:4326", always_xy=True).transform


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parts(geometry, kind):
    if geometry.is_empty:
        return []
    if geometry.geom_type == kind:
        return [geometry]
    return [p for g in getattr(geometry, "geoms", []) for p in parts(g, kind)]


def polygon(geometries):
    return unary_union([p for g in geometries for p in parts(make_valid(g), "Polygon")])


def write_geo(path, rows):
    features = [{"type": "Feature", "properties": attrs, "geometry": mapping(transform(TO_LONLAT, geom))}
                for attrs, geom in rows if not geom.is_empty]
    dump(path, {"type": "FeatureCollection", "features": features})


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        out = csv.DictWriter(f, fields)
        out.writeheader()
        out.writerows(rows)


class Transport:
    def __init__(self, root):
        self.root = root.resolve()
        manifest = load(ARCHIVE / "manifest.json")
        self.product = manifest["product"]
        municipalities = ARCHIVE / "floodlab-icv-study.geojson"
        if digest(municipalities) != manifest["municipal_subset_sha256"]:
            raise ValueError("Municipality archive checksum mismatch")
        self.observation_hash = manifest["observation_sha256"]
        self.sources = {"product": PROD, "product_id": self.product["id"], "observation_utc": self.product["images"][0]["acquisitionTime"] + "Z",
                        "sensor": self.product["images"][0]["sensorName"], "delivery_utc": self.product["version"]["deliveryTime"] + "Z",
                        "observation_layer": next(x["json"] for x in self.product["layers"] if "observedEventA" in x["name"]),
                        "observation_sha256": self.observation_hash, "municipal_source": ICV, "municipal_source_sha256": manifest["municipal_source_sha256"],
                        "municipal_subset_sha256": digest(municipalities), "roads_snapshot_utc": "2024-10-28T23:59:59Z",
                        "roads_sha256": digest(BASE / "roads-20241028.geojson"), "road_metadata_sha256": digest(BASE / "osm-roads-response.json"),
                        "road_window_sha256": digest(BASE / "study-area.geojson")}
        self.towns = {}
        self.window = polygon(transform(TO_METRIC, shape(f["geometry"])) for f in load(BASE / "study-area.geojson")["features"])
        self.support = self.window.intersection(polygon([transform(TO_METRIC, from_wkt(self.product["extent"]))]))
        for f in load(municipalities)["features"]:
            p = f["properties"]
            geom = polygon([transform(TO_METRIC, shape(f["geometry"]))])
            if geom.intersection(self.window).area > 1:
                self.towns[p["cod_ine_mun"]] = (p["nom_mun"], geom)

    def inventory(self):
        return {**self.sources, "allowed_major_classes": CLASSES, "allowed_local_classes": LOCAL_CLASSES, "towns": [
            {"code": code, "name": name, "coverage_pct_of_town": round(100 * geom.intersection(self.support).area / geom.area, 3)}
            for code, (name, geom) in sorted(self.towns.items())],
            "coverage_definition": "intersection of EMS product mapped extent, archived road query window and municipality; not satellite pixel validity"}

    def screen(self, product, town_codes, road_classes, output_id):
        if product != PROD or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", output_id):
            raise ValueError("Unknown product or invalid analysis ID")
        if not set(town_codes) <= self.towns.keys() or not road_classes or not set(road_classes) <= set(CLASSES + LOCAL_CLASSES):
            raise ValueError("Use returned town codes and explicit supported road classes")
        out = self.root / output_id
        out.mkdir(parents=True, exist_ok=False)
        tags = {str(r["id"]): r.get("tags", {}) for r in load(BASE / "osm-roads-response.json")["elements"] if r["type"] == "way"}
        roads = [(f["properties"], transform(TO_METRIC, shape(f["geometry"])))
                 for f in load(BASE / "roads-20241028.geojson")["features"] if f["properties"]["highway"] in road_classes]
        roads.sort(key=lambda p: (not bool(p[0]["name"] or tags.get(p[0]["osm_id"], {}).get("ref")), int(p[0]["osm_id"])))
        groups = defaultdict(list)
        raw = gzip.decompress((ARCHIVE / "floodlab-emsr773-aoi03-observed.json.gz").read_bytes())
        if hashlib.sha256(raw).hexdigest() != self.observation_hash:
            raise ValueError("Observation archive checksum mismatch")
        for f in json.loads(raw)["features"]:
            kind = f["properties"]["notation"]
            if kind in ("Flooded area", "Flood trace"):
                projected = transform(TO_METRIC, shape(f["geometry"]))
                if projected.intersects(self.support):
                    groups[kind].append(projected)
        wet = polygon(groups["Flooded area"]).intersection(self.support)
        trace = polygon(groups["Flood trace"]).intersection(self.support).difference(wet)
        if wet.is_empty or trace.is_empty:
            raise ValueError("Expected both official polygon classes in the archived study window")

        rows, assessable, summaries, scopes = [], [], [], []
        # Remove coincident geometry from the remaining candidate road list, including exact boundary overlaps.
        assigned = unary_union([])
        for code in sorted(set(town_codes) if town_codes else self.towns):
            name, geom = self.towns[code]
            support = geom.intersection(self.support)
            town_rows, baseline = [], []
            for p, line in roads:
                if support.is_empty or not line.intersects(support):
                    continue
                inside = line.intersection(support).difference(assigned)
                lines = parts(inside, "LineString")
                if not lines:
                    continue
                baseline.extend(lines)
                assigned = assigned.union(unary_union(lines))
                assessable.extend(({"town_code": code, "town": name}, g) for g in lines)
                for evidence, flood in (("flooded_area", wet), ("flood_trace", trace)):
                    intersected = inside.intersection(flood)
                    if evidence == "flood_trace":
                        intersected = intersected.difference(wet)
                    segments = sorted(parts(intersected, "LineString"), key=lambda g: (g.bounds, g.wkb))
                    for g in segments:
                        if g.length <= 1e-5:
                            continue
                        lon, lat = TO_LONLAT(*g.interpolate(.5, normalized=True).coords[0][:2])
                        osm_id = p["osm_id"]
                        flags = [key for key in ("bridge", "tunnel") if p.get(key, "") not in ("", "no", None)]
                        tag = tags.get(osm_id, {})
                        stable = min(g.wkb, g.reverse().wkb)
                        uid = hashlib.sha256(stable).hexdigest()[:10]
                        attrs = {"segment_id": f"{code}-{osm_id}-{uid}", "town_code": code, "town": name,
                                 "road_name": p.get("name") or "", "road_ref": tag.get("ref", ""), "osm_way": osm_id,
                                 "highway": p["highway"], "evidence": evidence, "structure": "+".join(flags),
                                 "overlap_m": g.length, "longitude": round(lon, 6), "latitude": round(lat, 6)}
                        town_rows.append((attrs, g))
            rows.extend(town_rows)
            distances = {e: sum(r[0]["overlap_m"] for r in town_rows if r[0]["evidence"] == e) for e in ("flooded_area", "flood_trace")}
            evaluated = sum(g.length for g in baseline)
            exposed = sum(distances.values())
            if exposed > evaluated + 0.01:
                raise ValueError("Overlap exceeds assessed road length")
            summaries.append({"town_code": code, "town": name, "town_km2": geom.area / 1e6, "mapped_km2": support.area / 1e6,
                              "coverage_pct": 100 * support.area / geom.area, "assessed_m": evaluated,
                              "flooded_area_m": distances["flooded_area"], "flood_trace_m": distances["flood_trace"], "exposed_m": exposed,
                              "exposed_pct": 100 * exposed / evaluated if evaluated else None, "segments": len(town_rows),
                              "structure_m": sum(r[0]["overlap_m"] for r in town_rows if r[0]["structure"])})
            scopes.append(({"town_code": code, "town": name}, support))
        rows.sort(key=lambda r: (r[0]["overlap_m"] < 1, bool(r[0]["structure"]), r[0]["evidence"] != "flooded_area",
                                 -r[0]["overlap_m"], r[0]["segment_id"]))
        for i, (a, _) in enumerate(rows, start=1):
            a["review_order"] = i
        fields = list(rows[0][0]) if rows else ["segment_id", "town_code", "town", "osm_way", "overlap_m"]
        write_csv(out / "segments.csv", [r[0] for r in rows], fields)
        write_csv(out / "municipalities.csv", summaries, list(summaries[0]))
        corridors = {}
        for a, _ in rows:
            identifier = a["road_ref"] or a["road_name"] or f'OSM {a["osm_way"]}'
            key = (a["town_code"], identifier)
            c = corridors.setdefault(key, {"town_code": a["town_code"], "town": a["town"], "road": identifier,
                                          "flooded_area_m": 0.0, "flood_trace_m": 0.0, "structure_m": 0.0, "segments": 0})
            c[a["evidence"] + "_m"] += a["overlap_m"]
            c["structure_m"] += a["overlap_m"] if a["structure"] else 0.0
            c["segments"] += 1
        for c in corridors.values():
            c["exposed_m"] = c["flooded_area_m"] + c["flood_trace_m"]
        ordered = sorted(corridors.values(), key=lambda c: (-c["exposed_m"], c["town_code"], c["road"]))
        if ordered:
            write_csv(out / "corridors.csv", ordered, list(ordered[0]))
        write_geo(out / "segments.geojson", rows)
        write_geo(out / "assessed_roads.geojson", assessable)
        write_geo(out / "coverage.geojson", scopes)
        write_geo(out / "town_boundaries.geojson", [(a, self.towns[a["town_code"]][1]) for a, _ in scopes])
        selected_support = unary_union([g for _, g in scopes])
        write_geo(out / "observed.geojson", [({"evidence": e}, g.intersection(selected_support))
                    for e, g in (("flooded_area", wet), ("flood_trace", trace))])
        result = {"analysis_id": output_id, "sources": self.sources, "scope_codes": [r["town_code"] for r in summaries],
                  "road_classes": road_classes, "crs": METRIC, "municipalities": summaries, "segments": len(rows),
                  "priority_basis": "Give sub-metre fragments lower display priority; keep structures in a separate manual review lane; then flooded area before trace and descending length. Not passability.",
                  "priority": [r[0] for r in rows[:10]], "corridors": ordered[:8],
                  "structures": [r[0] for r in rows if r[0]["structure"]][:4],
                  "files": {f.name: digest(f) for f in out.iterdir() if f.is_file()}}
        dump(out / "analysis.json", result)
        return result

    def render(self, analysis_id, map_title, top_n):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", analysis_id) or not 1 <= top_n <= 10 or not 1 <= len(map_title) <= 68 or not map_title.isascii():
            raise ValueError("Use an existing analysis ID, a short English title and 1 to 10 review labels")
        out = self.root / analysis_id
        if not (out / "analysis.json").is_file() or (out / "map.json").exists():
            raise ValueError("Use a completed analysis without a published map")
        subprocess.run([str(executable("python")), str(ROOT / "transport_layout.py"), str(out), map_title, str(top_n)],
                       env=environment(), check=True, capture_output=True, timeout=100)
        files = {f.name: digest(f) for f in sorted(out.iterdir())
                 if f.name in {"transport.pdf", "transport.png", "transport.qgz", "north.svg", "review_points.geojson"}}
        manifest = {"analysis_id": analysis_id, "top_n": top_n, "files": files, "map_title": map_title}
        dump(out / "map.json", manifest)
        return {**manifest, "kind": "transport_screening", "key": analysis_id,
                "aoi": map_title, "observations": [self.sources["observation_utc"]]}
