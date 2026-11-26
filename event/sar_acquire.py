"""Fetch real Sentinel-1 RTC VV/VH pixel windows and align them on a metric grid."""
import concurrent.futures
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler

import numpy as np
from osgeo import gdal, osr
from pyproj import Transformer

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc"
HOST = "sentinel1euwestrtc.blob.core.windows.net"
BBOX = (-.52, 39.37, -.36, 39.46)
RESOLUTION = 20
DATES = ("2024-10-08", "2024-10-20", "2024-11-01")
ORBIT = 110
EPSG = 32630


def get_json(url):
    for connection in (build_opener(), build_opener(ProxyHandler({}))):
        try:
            with connection.open(Request(url, headers={"User-Agent": "FloodLab-Remote-Sensing/1.0"}), timeout=16) as response:
                return json.load(response)
        except (OSError, ValueError):
            continue
    raise RuntimeError("Planetary Computer catalog or download service unavailable")


def bounds():
    ll = Transformer.from_crs("EPSG:4326", f"EPSG:{EPSG}", always_xy=True).transform_bounds(*BBOX)
    return tuple(float((np.floor if i < 2 else np.ceil)(v / RESOLUTION) * RESOLUTION) for i, v in enumerate(ll))


def fetch(day, out, token):
    q = urlencode({"collections": "sentinel-1-rtc", "bbox": ",".join(map(str, BBOX)),
                   "datetime": f"{day}T00:00:00Z/{day}T23:59:59Z", "limit": 50})
    features = get_json(STAC + "/search?" + q)["features"]
    matched = sorted((i for i in features if i["properties"].get("sat:relative_orbit") == ORBIT
                      and {"vv", "vh"} <= i["assets"].keys()), key=lambda i: i["id"])
    if not matched:
        raise RuntimeError("No matching dual-polarisation RTC scene on " + day)
    target_bounds = bounds()
    width, height = int((target_bounds[2] - target_bounds[0]) / RESOLUTION), int((target_bounds[3] - target_bounds[1]) / RESOLUTION)
    arrays = np.full((2, height, width), np.nan, dtype="float32")
    used = []
    for record in matched:
        links = {band: record["assets"][band]["href"] for band in ("vv", "vh")}
        if any(urlsplit(url).hostname != HOST or urlsplit(url).scheme != "https" or urlsplit(url).query for url in links.values()):
            raise ValueError("RTC asset host or path not supported")
        if np.isfinite(arrays).all():
            break
        contributed = False
        for index, href in enumerate(links.values()):
            try:
                ds = gdal.Open("/vsicurl/" + href + "?" + token)
                tile = gdal.Warp("", ds, format="MEM", dstSRS=f"EPSG:{EPSG}", outputBounds=target_bounds,
                                 xRes=RESOLUTION, yRes=RESOLUTION, dstNodata=float("nan"), outputType=gdal.GDT_Float32)
                linear = tile.ReadAsArray()
            except (RuntimeError, AttributeError):
                raise RuntimeError("Sentinel-1 window transfer failed on " + day) from None
            good = (linear > 0) & np.isfinite(linear) & ~np.isfinite(arrays[index])
            if good.any():
                arrays[index][good] = 10 * np.log10(linear[good])
                contributed = True
        if contributed:
            used.append({"id": record["id"], "utc": record["properties"]["datetime"], "assets": links})
    valid = np.all(np.isfinite(arrays), axis=0)
    if valid.mean() < .5:
        raise RuntimeError("Insufficient dual-polarisation coverage on " + day)
    name = f"s1-{day}.tif"
    raster = gdal.GetDriverByName("GTiff").Create(str(out / name), width, height, 2, gdal.GDT_Float32,
                                                  ["COMPRESS=DEFLATE", "TILED=YES"])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(EPSG)
    raster.SetProjection(srs.ExportToWkt())
    raster.SetGeoTransform((target_bounds[0], RESOLUTION,0,target_bounds[3],0,-RESOLUTION))
    for idx, band in enumerate(("VV", "VH")):
        b = raster.GetRasterBand(idx + 1)
        b.SetDescription(band + " (dB)")
        b.SetNoDataValue(float("nan"))
        b.WriteArray(arrays[idx])
    raster.FlushCache()
    raster = None
    return {"date": day, "file": name, "relative_orbit": ORBIT, "items": used,
            "sha256": hashlib.sha256((out / name).read_bytes()).hexdigest(),
            "valid_fraction": float(valid.mean())}


def download(out, before, after):
    if before not in DATES[:2] or after != DATES[-1]:
        raise ValueError("Select an archived pre-event date and the supported post-event date")
    gdal.UseExceptions()
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    for k, v in {"GDAL_DISABLE_READDIR_ON_OPEN":"EMPTY_DIR", "GDAL_HTTP_TIMEOUT":"30",
                 "GDAL_HTTP_MAX_RETRY":"1", "GDAL_HTTP_MULTIRANGE":"SERIAL",
                 "CPL_VSIL_CURL_CHUNK_SIZE":"262144", "CPL_VSIL_CURL_CACHE_SIZE":"33554432",
                 "GDAL_HTTP_NOPROXY":HOST}.items():
        gdal.SetConfigOption(k, v)
    token = get_json(TOKEN)["token"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(fetch, day, out, token) for day in (before, after)]
        captures = [j.result() for j in jobs]
    payload = {"captures": captures, "bbox_lon_lat": BBOX, "resolution_m": RESOLUTION,
               "crs": f"EPSG:{EPSG}", "mode": "live_remote_window", "catalog": STAC,
               "units": "10 log10 calibrated power", "relative_orbit": ORBIT}
    (out / "download.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        download(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
