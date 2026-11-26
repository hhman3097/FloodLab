"""Segment independent sensors, compare evidence, and measure exposure in UTM 34N."""
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr
from pyproj import Transformer

from karla_data import CELL, EPSG, FOCUS, WINDOW, dump, sha
from sar_analysis import median3, raster_like, srs


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def source(folder):
    rows = read(folder / "download.json")["captures"]
    datasets = []
    for row in rows:
        path = folder / row["file"]
        if sha(path) != row["sha256"]:
            raise ValueError("Source raster checksum mismatch")
        datasets.append(gdal.Open(str(path)))
    return datasets


def write_mask(base, folder, name, data, legend):
    if data.shape != (base.RasterYSize, base.RasterXSize):
        raise ValueError("Mask is misaligned")
    ds = raster_like(base, data.astype("uint8"), folder / (name + ".tif"))
    ds.GetRasterBand(1).SetNoDataValue(255)
    ds.SetMetadataItem("CLASSES", legend)
    ds.FlushCache()


def classify(before, after, algorithm):
    if algorithm == "sar_change":
        b, a = (np.stack([median3(band) for band in row]) for row in (before, after))
        valid = np.isfinite(before).all(axis=0) & np.isfinite(after).all(axis=0)
        water = (a[0] < -16) & (a[1] < -23)
        baseline = (b[0] < -16) & (b[1] < -23)
        return valid, water & ~baseline & ((b[0] - a[0]) > 3), baseline
    if algorithm != "optical_mndwi":
        raise ValueError("Unsupported segmentation method")

    def optical(x):
        den = x[:2].sum(axis=0)
        valid = np.isfinite(x).all(axis=0) & np.isin(x[2], [4, 5, 6]) & (den > .015)
        score = np.divide(x[0] - x[1], den, out=np.zeros_like(den), where=valid)
        return valid, score > .12

    pv, water_before = optical(before)
    av, water_after = optical(after)
    return pv & av, water_after & ~water_before, water_before


def polys(base, path, mask, minimum=9):
    filtered = np.zeros(mask.shape, dtype="uint8")
    tmp = raster_like(base, mask.astype("uint8"))
    datasource = ogr.GetDriverByName("Memory").CreateDataSource("patches")
    layer = datasource.CreateLayer("regions", srs(EPSG), ogr.wkbPolygon)
    gdal.Polygonize(tmp.GetRasterBand(1), tmp.GetRasterBand(1), layer, -1, ["8CONNECTED=8"])
    result = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    out = result.CreateLayer("candidates", srs(EPSG), ogr.wkbMultiPolygon)
    out.CreateField(ogr.FieldDefn("area_m2", ogr.OFTReal))
    count, total = 0, 0.0
    for feature in layer:
        geom = feature.GetGeometryRef()
        area = geom.GetArea()
        if area < CELL*CELL*minimum:
            continue
        valid = geom if geom.IsValid() else geom.MakeValid()
        multi = ogr.Geometry(ogr.wkbMultiPolygon)
        stack = [valid]
        while stack:
            part = stack.pop()
            if part.GetGeometryName() == "POLYGON":
                multi.AddGeometry(part)
            elif part.GetGeometryName() in ("MULTIPOLYGON", "GEOMETRYCOLLECTION"):
                stack.extend(part.GetGeometryRef(i) for i in range(part.GetGeometryCount()))
        if abs(multi.GetArea() - area) > .05:
            raise ValueError("Geometry repair changed pixel area")
        f = ogr.Feature(out.GetLayerDefn())
        f.SetField("area_m2", area)
        f.SetGeometry(multi)
        out.CreateFeature(f)
        count += 1
        total += area
    blank = raster_like(base, filtered)
    gdal.RasterizeLayer(blank, [1], out, burn_values=[1])
    kept = blank.ReadAsArray() == 1
    if abs(total - kept.sum()*CELL*CELL) > .05 or (kept & ~mask).any():
        raise ValueError("Raster and vector area do not agree")
    return kept, count, total


def segment(inputs, folder, algorithm):
    provenance = read(inputs / "download.json")
    if (provenance["sensor"], algorithm) not in (("sar","sar_change"), ("optical","optical_mndwi")):
        raise ValueError("Algorithm and downloaded sensor do not match")
    before, after = source(inputs)
    if before.GetGeoTransform() != after.GetGeoTransform() or before.GetProjection() != after.GetProjection():
        raise ValueError("Observations are not aligned")
    valid, candidate, baseline = classify(before.ReadAsArray(), after.ReadAsArray(), algorithm)
    if valid.mean() < .65:
        raise ValueError("Less than 65% of the AOI has interpretable pre/post pixels")
    kept, patches, vector_area = polys(before, folder / "candidates.gpkg", valid & candidate)
    mask = np.where(valid, np.where(kept, 1, np.where(baseline, 2, 0)), 255)
    write_mask(before, folder, "evidence", mask, "0=valid/no candidate;1=candidate;2=pre-existing water proxy;255=uninterpretable")
    stats = {"algorithm": algorithm, "dates": [r["date"] for r in provenance["captures"]], "input_id": inputs.name,
             "patches": patches, "candidate_km2": vector_area/1e6, "candidate_pixels": int(kept.sum()),
             "valid_pixels": int(valid.sum()), "valid_pct": float(valid.mean()*100),
             "baseline_km2": float((mask==2).sum()*CELL*CELL/1e6), "evidence_sha256": sha(folder/"evidence.tif")}
    dump(folder / "results.json", stats)


def overlay(sar, optical, target):
    ds = gdal.Open(str(sar / "evidence.tif"))
    ds2 = gdal.Open(str(optical / "evidence.tif"))
    if ds.GetGeoTransform() != ds2.GetGeoTransform() or ds.GetProjection() != ds2.GetProjection():
        raise ValueError("SAR and optical grids differ")
    a, b = ds.ReadAsArray(), ds2.ReadAsArray()
    joint, s, o = (a != 255) & (b != 255), a == 1, b == 1
    evidence = np.select([~joint, s & o, s, o], [255, 1, 2, 3], default=0)
    write_mask(ds, target, "agreement", evidence, "0=neither;1=both;2=SAR only;3=optical only;255=not comparable")
    stats = {"both_km2": float((evidence==1).sum()*CELL*CELL/1e6),
             "sar_only_km2": float((evidence==2).sum()*CELL*CELL/1e6),
             "optical_only_km2": float((evidence==3).sum()*CELL*CELL/1e6),
             "unknown_km2": float((evidence==255).sum()*CELL*CELL/1e6),
             "joint_pct": float(joint.mean()*100), "sar_candidate_outside_joint_km2": float((s & ~joint).sum()*CELL*CELL/1e6),
             "dates": ["2023-12-18","2023-12-19"], "interpretation": "Cross-date cross-sensor evidence; never infer expansion or recession from these differences.",
             "sar_id": sar.name, "optical_id": optical.name}
    dump(target / "results.json", stats)


def official_comparison(mask_path, ref_path, layer_name):
    dataset = gdal.Open(str(mask_path))
    reference = ogr.Open(str(ref_path))
    pixels = dataset.ReadAsArray()
    def rasterize(layer):
        out = raster_like(dataset)
        gdal.RasterizeLayer(out, [1], reference.GetLayerByName(layer), burn_values=[1])
        return out.ReadAsArray() == 1
    support = rasterize("P00_aoi") & (pixels != 255)
    official = rasterize(layer_name)
    candidate = pixels == 1
    counts = {key:int((arr & support).sum()) for key,arr in
              {"common":np.ones(pixels.shape,dtype=bool), "both":official & candidate,
               "official_only":official & ~candidate, "candidate_only":candidate & ~official}.items()}
    denominator = counts["both"] + counts["official_only"] + counts["candidate_only"]
    return {"pixels":counts,"iou":counts["both"]/denominator if denominator else None,
            "layer":layer_name,"reference_sha256":sha(ref_path),
            "boundary":"Agreement inside official AOI and valid pixels; not ground truth."}


def road_features(path, window):
    from shapely.geometry import box, shape
    from shapely.ops import transform
    tx = Transformer.from_crs(4326, EPSG, always_xy=True).transform
    area = transform(tx, box(*window))
    eligible = {"motorway", "trunk", "primary", "secondary", "tertiary"}
    for row in read(path)["features"]:
        tags = row["properties"]
        if not tags.get("@osmId","").startswith("way/") or tags.get("highway", "").removesuffix("_link") not in eligible:
            continue
        line = shape(row["geometry"])
        mapped = transform(tx,line).intersection(area)
        if not mapped.is_empty and mapped.length:
            yield int(tags["@osmId"].split("/")[-1]), tags, mapped


def exposure(comparison, farmland, roads, folder, scope):
    from shapely import from_wkb, union_all
    from shapely.geometry import mapping
    from shapely.ops import transform

    dataset = gdal.Open(str(comparison / "agreement.tif"))
    codes = dataset.ReadAsArray()
    land = source(farmland)[0]
    if land.GetGeoTransform() != dataset.GetGeoTransform():
        raise ValueError("Land cover grid differs")
    window = FOCUS if scope == "north" else WINDOW
    if scope not in ("north", "study"):
        raise ValueError("Unknown study scope")
    rr, cc = np.indices(codes.shape)
    gt = dataset.GetGeoTransform()
    transform_xy = Transformer.from_crs(EPSG,4326,always_xy=True)
    lon,lat = transform_xy.transform(gt[0]+(cc+.5)*CELL, gt[3]-(rr+.5)*CELL)
    inside = (lon >= window[0]) & (lon <= window[2]) & (lat >= window[1]) & (lat <= window[3])
    cropland = (land.ReadAsArray() == 40) & inside
    classes = {1:"both", 2:"sar_only", 3:"optical_only", 255:"unknown"}
    farm = {name: float(np.count_nonzero(cropland & (codes==i))*CELL*CELL/1e6) for i,name in classes.items()}
    rows, features = [], []
    segments = list(road_features(roads, window))
    # Union separately by evidence class before intersecting roads to avoid duplicate lengths.
    polygons = {}
    for i in classes:
        tmp = raster_like(dataset, ((codes==i) & inside).astype("uint8"))
        mem = ogr.GetDriverByName("Memory").CreateDataSource("roads")
        shape = mem.CreateLayer("areas", srs(EPSG), ogr.wkbPolygon)
        gdal.Polygonize(tmp.GetRasterBand(1),tmp.GetRasterBand(1),shape,-1,[])
        polygons[i] = union_all([from_wkb(bytes(f.GetGeometryRef().ExportToWkb())) for f in shape])
    assigned = None
    for osm, tags, line in sorted(segments, key=lambda r:(not bool(r[1].get("ref") or r[1].get("name")),r[0])):
        if assigned is not None:
            line = line.difference(assigned)
        if line.is_empty or not line.length:
            continue
        assigned = union_all([assigned,line]) if assigned is not None else line
        for i, label in classes.items():
            intersect = line.intersection(polygons[i])
            pieces = [intersect]
            while pieces:
                part = pieces.pop()
                if part.is_empty:
                    continue
                if part.geom_type != "LineString":
                    pieces.extend(getattr(part,"geoms", []))
                    continue
                if part.length < .01:
                    continue
                pt = transform(transform_xy.transform, part.interpolate(.5, normalized=True))
                structure = "/".join(k for k in ("bridge","tunnel") if tags.get(k) not in (None,"","no"))
                row = {"road":tags.get("ref") or tags.get("name") or f"OSM {osm}", "osm_way":osm,
                       "highway":tags["highway"], "evidence":label, "length_m":part.length,
                       "lon":round(pt.x,6),"lat":round(pt.y,6),"structure":structure}
                rows.append(row)
                features.append({"type":"Feature","properties":row,"geometry":mapping(transform(transform_xy.transform,part))})
            line = line.difference(polygons[i])
    rows.sort(key=lambda r: (r["evidence"] != "both", r["evidence"] == "unknown", -r["length_m"]))
    for index,row in enumerate(rows,1):
        row["id"] = index
    dump(folder/"roads.geojson",{"type":"FeatureCollection","features":features})
    with (folder/"roads.csv").open("w",encoding="utf-8-sig",newline="") as out:
        fields = ["id","road","osm_way","highway","evidence","length_m","lon","lat","structure"]
        writer = csv.DictWriter(out,fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with (folder/"cropland.csv").open("w",encoding="utf-8",newline="") as out:
        writer=csv.writer(out)
        writer.writerow(["evidence","cropland_km2"])
        writer.writerows(farm.items())
    # A single-sensor baseline can identify only SAR positives; it cannot supply a second observation.
    road_summary={label:sum(r["length_m"] for r in rows if r["evidence"]==label) for label in classes.values()}
    result={"scope":scope, "bbox":window, "input_comparison_id":comparison.name,
            "cropland_total_km2":float(cropland.sum()*CELL*CELL/1e6),"cropland_km2":farm,
            "road_m":road_summary,"segments":len(rows), "road_shortlist":rows[:6],
            "cropland_source":"landcover.tif","roads_sha256":sha(roads),
            "boundary":"Centerline overlap is not an observation of road closure or passability."}
    dump(folder / "results.json", result)


if __name__ == "__main__":
    gdal.UseExceptions()
    ogr.UseExceptions()
    task,*args=sys.argv[1:]
    if task == "segment":
        segment(Path(args[0]),Path(args[1]),args[2])
    elif task=="compare":
        overlay(*map(Path,args))
    elif task=="exposure":
        exposure(*map(Path,args[:-1]),args[-1])
    elif task=="official":
        print(json.dumps(official_comparison(Path(args[0]),Path(args[1]),args[2])))
