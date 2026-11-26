"""Case-specific, reproducible acquisition for the Karla flood recovery study."""
import concurrent.futures
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import numpy as np
from osgeo import gdal, osr
from pyproj import Transformer

from sar_acquire import STAC, get_json

WINDOW = (22.55, 39.43, 22.85, 39.65)
FOCUS = (22.63, 39.49, 22.78, 39.60)
EPSG = 32634
CELL = 20
DATES = {"sar": ("2023-09-01", "2023-12-18"), "optical": ("2023-08-31", "2023-12-19")}
COLLECTIONS = {"sar": "sentinel-1-rtc", "optical": "sentinel-2-l2a", "landcover": "esa-worldcover"}
ASSETS = {"sar": ("vv", "vh"), "optical": ("B03", "B11", "SCL"), "landcover": ("map",)}


def dump(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def grid(window=WINDOW):
    edges = Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True).transform_bounds(*window)
    return tuple(int((np.floor if i < 2 else np.ceil)(v / CELL) * CELL) for i, v in enumerate(edges))


def safe_remote(href):
    u = urlsplit(href)
    return u.scheme == "https" and not u.query and not u.username and (
        (u.hostname or "").endswith(".blob.core.windows.net") or (u.hostname or "").endswith(".amazonaws.com"))


def read_asset(href, token, nearest):
    if not safe_remote(href):
        raise ValueError("Unexpected STAC asset host")
    path = "/vsicurl/" + href + ("?" + token if token else "")
    for _ in range(3):
        try:
            result = gdal.Warp("", gdal.Open(path), format="MEM", dstSRS=f"EPSG:{EPSG}",
                               outputBounds=grid(), xRes=CELL, yRes=CELL,
                               resampleAlg="near" if nearest else "bilinear", outputType=gdal.GDT_Float32,
                               dstNodata=float("nan"))
            return result.ReadAsArray()
        except RuntimeError:
            gdal.VSICurlClearCache()
    raise RuntimeError("Could not read the requested pixel window") from None


def capture(sensor, day, folder, token):
    start, end = ("2021-01-01", "2021-12-31") if sensor == "landcover" else (day, day)
    query = STAC + "/search?" + urlencode({"collections": COLLECTIONS[sensor], "bbox": ",".join(map(str, WINDOW)),
                                           "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "limit": 50})
    data = get_json(query)
    matched = [i for i in data["features"] if set(ASSETS[sensor]) <= i["assets"].keys()
               and (sensor != "sar" or i["properties"].get("sat:relative_orbit") == 7)]
    if not matched:
        raise ValueError("No matching public assets on " + day)
    # First mosaic lower-cloud scenes; keep the contributing bands from the same source pixel.
    if sensor == "optical":
        by_tile = {}
        for item in sorted(matched, key=lambda row:row["id"],reverse=True):
            by_tile.setdefault(item["properties"].get("s2:mgrs_tile", item["id"].split("_")[-2]), item)
        matched=list(by_tile.values())
    matched.sort(key=lambda i: (i["properties"].get("eo:cloud_cover", 0), i["id"]))
    x0, y0, x1, y1 = grid()
    target = np.full((len(ASSETS[sensor]), (y1-y0)//CELL, (x1-x0)//CELL), np.nan, dtype="float32")
    scenes = []
    for item in matched:
        links = {name: item["assets"][name]["href"] for name in ASSETS[sensor]}
        arrays = np.stack([read_asset(href, token, sensor in ("sar", "landcover") or name == "SCL")
                           for name, href in links.items()])
        available = np.isfinite(arrays).all(axis=0) & (arrays > 0).all(axis=0)
        if sensor == "optical":
            good = available & np.isin(arrays[2], [4, 5, 6])
            raw_present = np.isfinite(target).all(axis=0)
            take = available & (~raw_present | (good & ~np.isin(target[2], [4, 5, 6])))
        else:
            take = available & ~np.isfinite(target).all(axis=0)
        if not take.any():
            continue
        if sensor == "sar":
            with np.errstate(invalid="ignore", divide="ignore"):
                arrays = 10 * np.log10(arrays)
        elif sensor == "optical":
            for i, band in enumerate(ASSETS[sensor][:2]):
                attrs = item["assets"][band].get("raster:bands", [{}])[0]
                # The archive's STAC raster metadata defines the processing-baseline offset.
                arrays[i] = arrays[i]*attrs.get("scale", .0001) + attrs.get("offset", -.1)
        target[:, take] = arrays[:, take]
        scenes.append({"id": item["id"], "datetime": item["properties"].get("datetime"),
                       "relative_orbit": item["properties"].get("sat:relative_orbit"), "assets": links,
                       "stac_raster_bands": {n: item["assets"][n].get("raster:bands", []) for n in links}})
        if (np.isin(target[2], [4, 5, 6]).all() if sensor == "optical" else np.isfinite(target).all()):
            break
    name = f"{sensor}-{day}.tif"
    path = folder / name
    raster = gdal.GetDriverByName("GTiff").Create(str(path), target.shape[2], target.shape[1], target.shape[0],
                                                  gdal.GDT_Float32, ["COMPRESS=DEFLATE", "TILED=YES"])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    raster.SetProjection(srs.ExportToWkt())
    raster.SetGeoTransform((x0, CELL, 0, y1, 0, -CELL))
    for i, band in enumerate(ASSETS[sensor], 1):
        layer = raster.GetRasterBand(i)
        layer.SetDescription(band)
        layer.SetNoDataValue(float("nan"))
        layer.WriteArray(target[i-1])
    raster = None
    valid = np.isfinite(target).all(axis=0)
    if sensor == "optical":
        valid &= np.isin(target[2], [4, 5, 6]) & (target[:2].sum(axis=0) > .015)
    return {"file": name, "date": day, "sensor": sensor, "valid_pct": float(valid.mean()*100),
            "scenes": scenes, "catalog_request": query, "sha256": sha(path)}


def acquire(folder, sensor):
    gdal.UseExceptions()
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    for key, value in {
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "GDAL_HTTP_TIMEOUT": "25", "GDAL_HTTP_MAX_RETRY": "2",
        "GDAL_HTTP_MULTIRANGE": "SERIAL", "CPL_VSIL_CURL_CHUNK_SIZE": "262144",
        "GDAL_HTTP_NOPROXY": ".blob.core.windows.net,.amazonaws.com",
    }.items():
        gdal.SetConfigOption(key, value)
    collection = COLLECTIONS[sensor]
    token = get_json("https://planetarycomputer.microsoft.com/api/sas/v1/token/" + collection)["token"]
    dates = DATES.get(sensor, ("2021",))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(capture, sensor, d, folder, token) for d in dates]
        captures = [job.result() for job in jobs]
    dump(folder / "download.json", {"sensor": sensor, "bbox": WINDOW, "resolution_m": CELL,
                                    "crs": f"EPSG:{EPSG}", "captures": captures})


if __name__ == "__main__":
    try:
        acquire(Path(sys.argv[1]), sys.argv[2])
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        # Never log signed asset query strings.
        print(str(exc).split("?", 1)[0][:450], file=sys.stderr)
        raise SystemExit(1) from None
