"""Transparent Sentinel-1 change segmentation and independent area checks."""
import gzip
import hashlib
import json
import sys
import warnings
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

PRESETS = {"balanced": (-16, -23, 3, 12), "conservative": (-18, -24, 4, 20)}
ARCHIVE = Path(__file__).resolve().parent / "inputs/assets/transport"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def median3(band):
    windows = np.lib.stride_tricks.sliding_window_view(np.pad(band, 1, mode="edge"), (3, 3))
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "All-NaN slice encountered")
        return np.nanmedian(windows, axis=(-1, -2))


def classify(before, after, preset):
    vv, vh, drop, _ = PRESETS[preset]
    valid = np.isfinite(before).all(axis=0) & np.isfinite(after).all(axis=0)
    pre, post = [np.stack([median3(b) for b in data]) for data in (before, after)]
    already_dark = (pre[0] < vv) & (pre[1] < vh)
    candidate = valid & ~already_dark & (post[0] < vv) & (post[1] < vh) & (pre[0] - post[0] > drop)
    return valid, candidate


def srs(code):
    projection = osr.SpatialReference()
    projection.ImportFromEPSG(code)
    projection.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return projection


def raster_like(source, array=None, path=None):
    driver = gdal.GetDriverByName("GTiff" if path else "MEM")
    dst = driver.Create(str(path or ""), source.RasterXSize, source.RasterYSize, 1, gdal.GDT_Byte,
                        ["COMPRESS=DEFLATE", "TILED=YES"] if path else [])
    dst.SetProjection(source.GetProjection())
    dst.SetGeoTransform(source.GetGeoTransform())
    if array is not None:
        dst.GetRasterBand(1).WriteArray(array)
    return dst


def mem_polygons(name, projection):
    store = ogr.GetDriverByName("Memory").CreateDataSource(name)
    return store, store.CreateLayer(name, srs=projection, geom_type=ogr.wkbPolygon)


def area_polygons(mask, raster, path, minimum):
    projection = srs(32630)
    source = raster_like(raster, mask.astype("uint8"))
    cells = abs(raster.GetGeoTransform()[1] * raster.GetGeoTransform()[5])
    vector, polygons = mem_polygons("raw", projection)
    b = source.GetRasterBand(1)
    gdal.Polygonize(b, b, polygons, -1, ["8CONNECTED=8"])
    gpkg = ogr.GetDriverByName("GPKG").CreateDataSource(str(path))
    out = gpkg.CreateLayer("candidate", projection, ogr.wkbMultiPolygon)
    out.CreateField(ogr.FieldDefn("area_m2", ogr.OFTReal))
    area = 0.0
    count = 0
    for f in polygons:
        geom = f.GetGeometryRef()
        size = geom.GetArea()
        if size + 1e-6 >= minimum * cells:
            feature = ogr.Feature(out.GetLayerDefn())
            feature.SetField("area_m2", size)
            valid_geom = geom if geom.IsValid() else geom.MakeValid()
            multi = ogr.Geometry(ogr.wkbMultiPolygon)
            pending = [valid_geom]
            while pending:
                part = pending.pop()
                if part.GetGeometryName() == "POLYGON":
                    multi.AddGeometry(part)
                elif part.GetGeometryName() in ("MULTIPOLYGON", "GEOMETRYCOLLECTION"):
                    pending.extend(part.GetGeometryRef(i) for i in range(part.GetGeometryCount()))
            if multi.IsEmpty() or abs(multi.GetArea() - size) > 0.1:
                raise ValueError("Vector validity repair changed candidate area")
            feature.SetGeometry(multi)
            out.CreateFeature(feature)
            area += size
            count += 1
    clean = raster_like(raster)
    gdal.RasterizeLayer(clean, [1], out, burn_values=[1])
    return clean.ReadAsArray().astype(bool), area, count


def segment(source, target, preset):
    download = read_json(source / "download.json")
    before, after = download["captures"]
    rasters = [gdal.Open(str(source / row["file"])) for row in download["captures"]]
    if any(d is None or d.RasterCount != 2 for d in rasters):
        raise ValueError("Expected two VV/VH rasters")
    for record in download["captures"]:
        if record["sha256"] != digest(source / record["file"]):
            raise ValueError("Downloaded raster checksum mismatch")
    first, last = rasters
    if first.GetProjection() != last.GetProjection() or first.GetGeoTransform() != last.GetGeoTransform() or \
       (first.RasterYSize, first.RasterXSize) != (last.RasterYSize, last.RasterXSize):
        raise ValueError("Pre/post grid is not aligned")
    valid, raw = classify(first.ReadAsArray(), last.ReadAsArray(), preset)
    if valid.mean() < .5:
        raise ValueError("Less than half the common analysis grid is observed")
    vv, vh, drop, minimum = PRESETS[preset]
    filtered, area, count = area_polygons(raw, first, target / "candidate.gpkg", minimum)
    if (filtered & ~raw).any() or not filtered.any():
        raise ValueError("Unexpected polygon conversion or empty candidate mask")
    out = raster_like(first, np.where(valid, filtered, 255).astype("uint8"), target / "candidate.tif")
    out.GetRasterBand(1).SetNoDataValue(255)
    out.FlushCache()
    cell_m2 = download["resolution_m"] ** 2
    pixels = int(filtered.sum())
    if abs(pixels * cell_m2 - area) > 0.1:
        raise ValueError("Polygon and pixel areas disagree")
    result = {"algorithm": "dual-polarisation same-orbit change + stable-dark exclusion + 8-connected filtering",
              "preset": preset, "thresholds": {"post_vv_db_below": vv, "post_vh_db_below": vh, "vv_drop_db_above": drop, "min_pixels": minimum},
              "dates": [before["date"], after["date"]], "relative_orbit": download["relative_orbit"],
              "crs": download["crs"], "resolution_m": download["resolution_m"], "bbox_lon_lat": download["bbox_lon_lat"],
              "valid_fraction": float(valid.mean()), "valid_pixels": int(valid.sum()), "raw_candidate_pixels": int(raw.sum()),
              "candidate_pixels": pixels, "patches": count, "candidate_km2": pixels * cell_m2 / 1e6,
              "vector_area_km2": area / 1e6, "area_consistency_m2": abs(pixels * cell_m2 - area),
              "input": source.name, "files": {n: digest(target / n) for n in ("candidate.tif", "candidate.gpkg")}}
    save_json(target / "segmentation.json", result)


def burns(geometries, raster):
    store, layer = mem_polygons("rasterize", srs(32630))
    for geometry in geometries:
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)
    out = raster_like(raster)
    gdal.RasterizeLayer(out, [1], layer, burn_values=[1])
    return out.ReadAsArray().astype(bool)


def summarize(folder):
    result = read_json(folder / "segmentation.json")
    for name, expected in result["files"].items():
        if digest(folder / name) != expected:
            raise ValueError("Segmentation file checksum mismatch")
    source = gdal.Open(str(folder / "candidate.tif"))
    array = source.ReadAsArray()
    valid, candidate = array != 255, array == 1
    if int(candidate.sum()) != result["candidate_pixels"]:
        raise ValueError("Candidate mask pixel count differs from manifest")
    tx = osr.CoordinateTransformation(srs(4326), srs(32630))
    icv = ogr.Open(str(ARCHIVE / "floodlab-icv-study.geojson"))
    towns = []
    cell = result["resolution_m"] ** 2 / 1e6
    for feature in icv.GetLayer():
        geom = feature.GetGeometryRef().Clone()
        geom.Transform(tx)
        tm = burns([geom], source)
        n = int((candidate & tm).sum())
        if n:
            towns.append({"town": feature["nom_mun"], "code": feature["cod_ine_mun"], "pixels": n, "candidate_km2": n * cell})
    towns.sort(key=lambda r: (-r["pixels"], r["code"]))
    # The official product is an earlier observation, not validation truth.
    manifest = read_json(ARCHIVE / "manifest.json")
    extent = ogr.CreateGeometryFromWkt(manifest["product"]["extent"])
    extent.Transform(tx)
    common = valid & burns([extent], source)
    raw = gzip.decompress((ARCHIVE / "floodlab-emsr773-aoi03-observed.json.gz").read_bytes())
    if hashlib.sha256(raw).hexdigest() != manifest["observation_sha256"]:
        raise ValueError("Official comparison checksum mismatch")
    reference = []
    for item in json.loads(raw)["features"]:
        if item["properties"]["notation"] not in ("Flooded area", "Flood trace"):
            continue
        geom = ogr.CreateGeometryFromJson(json.dumps(item["geometry"]))
        geom.Transform(tx)
        if geom.Intersects(extent):
            reference.append(geom)
    ref = burns(reference, source) & common
    guess = candidate & common
    counts = {k: int(v.sum()) for k, v in {"both":guess & ref, "candidate_only":guess & ~ref,
                                            "reference_only":ref & ~guess, "common_valid":common}.items()}
    overlap_union = counts["both"] + counts["candidate_only"] + counts["reference_only"]
    zones = np.full(array.shape, 255, dtype="uint8")
    zones[common] = 0
    zones[guess & ref], zones[guess & ~ref], zones[ref & ~guess] = 1, 2, 3
    raster = raster_like(source, zones, folder / "agreement.tif")
    raster.GetRasterBand(1).SetNoDataValue(255)
    raster.SetMetadataItem("CLASSES", "0=neither;1=both;2=radar only;3=EMS only;255=uncompared")
    raster.FlushCache()
    comparison = {**counts, "iou": counts["both"] / overlap_union if overlap_union else None,
                  "support_km2": counts["common_valid"] * cell, "reference": "EMSR773 AOI03 GRA v2 (flooded area + flood trace)",
                  "observed_utc": manifest["product"]["images"][0]["acquisitionTime"] + "Z",
                  "interpretation": "Asynchronous spatial comparison, not ground-truth accuracy"}
    summary = {**result, "towns": towns, "reference_comparison": comparison}
    save_json(folder / "results.json", summary)
    return summary


if __name__ == "__main__":
    gdal.UseExceptions()
    ogr.UseExceptions()
    if sys.argv[1] == "segment":
        segment(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    elif sys.argv[1] == "summarize":
        summarize(Path(sys.argv[2]))
    else:
        raise SystemExit("Unsupported command")
