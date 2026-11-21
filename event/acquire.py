"""Acquire windowed public data and write an explicit, credential-free provenance record."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import rasterio as rio
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from rasterio.windows import from_bounds
from pyproj import Transformer

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "inputs"
CRS = "EPSG:32630"
BBOX = [-0.645, 39.23, -0.269, 39.505]
RES = 20
EMS = "https://mapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?code=EMSR773"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
S2 = "https://earth-search.aws.element84.com/v1"
DEM = ("https://copernicus-dem-30m.s3.amazonaws.com/"
       "Copernicus_DSM_COG_10_N39_00_W001_00_DEM/Copernicus_DSM_COG_10_N39_00_W001_00_DEM.tif")


def get(url):
    with urlopen(url, timeout=75) as response:
        return json.load(response)


def cache_json(name, url):
    path = RAW / name
    if not path.exists():
        path.write_text(json.dumps(get(url), ensure_ascii=False))
    return json.loads(path.read_text())


def search(base, collection, start, end, limit=100):
    query = urlencode({"collections": collection, "bbox": ",".join(map(str, BBOX)),
                       "datetime": f"{start}T00:00:00Z/{end}T23:59:59Z", "limit": limit})
    return get(f"{base}/search?{query}")["features"]


def grid():
    x0, y0, x1, y1 = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform_bounds(*BBOX)
    x0, y0 = np.floor(np.array([x0, y0]) / RES) * RES
    x1, y1 = np.ceil(np.array([x1, y1]) / RES) * RES
    return dict(driver="GTiff", crs=CRS, transform=from_origin(x0, y1, RES, RES),
                width=int((x1-x0)/RES), height=int((y1-y0)/RES), compress="deflate",
                tiled=True, blockxsize=256, blockysize=256)


def save(path, array, profile, nodata=None):
    array = np.asarray(array)
    if array.ndim == 2:
        array = array[None]
    with rio.open(path, "w", **profile, count=array.shape[0], dtype=array.dtype, nodata=nodata) as dst:
        dst.write(array)


def read_window(url, profile, method):
    with rio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_TIMEOUT=60,
                 GDAL_HTTP_MAX_RETRY=2, GDAL_HTTP_MULTIRANGE="SERIAL",
                 CPL_VSIL_CURL_CHUNK_SIZE=262144,
                 CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff"):
        with rio.open(url) as source:
            x0, y0, x1, y1 = rio.transform.array_bounds(
                profile["height"], profile["width"], profile["transform"])
            bounds = Transformer.from_crs(CRS, source.crs, always_xy=True).transform_bounds(x0,y0,x1,y1)
            window = from_bounds(*bounds, transform=source.transform).round_offsets().round_lengths()
            window = window.intersection(rio.windows.Window(0, 0, source.width, source.height))
            scale = 2 if source.crs.is_projected and abs(source.transform.a) < 15 else 1
            shape = tuple(max(1, int(n / scale)) for n in (window.height, window.width))
            src = source.read(1, window=window, out_shape=shape, resampling=method,
                              masked=True).astype("float32").filled(np.nan)
            transform = source.window_transform(window) * source.transform.scale(
                window.width / shape[1], window.height / shape[0])
            dst = np.full((profile["height"], profile["width"]), np.nan, dtype="float32")
            reproject(src, dst, src_transform=transform, src_crs=source.crs,
                      src_nodata=np.nan, dst_transform=profile["transform"], dst_crs=CRS,
                      dst_nodata=np.nan, resampling=method)
            return dst


def choose(items, date, orbit):
    found = [i for i in items if i["properties"]["datetime"].startswith(date)
             and i["properties"]["sat:relative_orbit"] == orbit]
    if not found:
        raise RuntimeError(f"No Sentinel-1 RTC scenes for relative orbit {orbit} on {date}")
    return sorted(found, key=lambda i: i["id"])


def sar(items, date, orbit, profile, token):
    sources = choose(items, date, orbit)
    path = RAW / f"sar-{date}-r{orbit}.tif"
    if not path.exists():
        print(f"Reading {date}, relative orbit {orbit} ({len(sources)} scenes)", flush=True)
        bands = []
        for band in ["vv", "vh"]:
            merged = np.full((profile["height"], profile["width"]), np.nan, dtype="float32")
            for source in sources:
                try:
                    a = read_window(f"{source['assets'][band]['href']}?{token}", profile, Resampling.nearest)
                except rio.errors.WindowError:
                    continue
                np.copyto(merged, a, where=(a > 0) & np.isfinite(a) & ~np.isfinite(merged))
            bands.append(10 * np.log10(merged))
        if not (np.isfinite(bands[0]) & np.isfinite(bands[1])).any():
            raise RuntimeError(f"No usable observations for {date} r{orbit}")
        save(path, np.stack(bands), profile, np.nan)
        print(path.name, np.isfinite(bands[0]).mean(), flush=True)
    return {"file": path.name, "date": date, "orbit": orbit,
            "items": [{"id": s["id"], "utc": s["properties"]["datetime"],
                       "assets": {k:s["assets"][k]["href"] for k in ("vv", "vh")}} for s in sources]}


def optical(profile):
    source = next(i for i in search(S2, "sentinel-2-l2a", "2024-10-26", "2024-10-26")
                  if i["id"] == "S2A_30SYJ_20241026_0_L2A")
    path = RAW / "context-s2-display.tif"
    if not path.exists():
        layers = []
        for name in ("red", "green", "blue"):
            asset = source["assets"][name]
            values = read_window(asset["href"], profile, Resampling.bilinear)
            good = np.isfinite(values)
            lo, hi = np.quantile(values[good], [.015, .985])
            scaled = 26 + 228 * np.clip((values-lo)/(hi-lo), 0, 1)**.55
            layers.append(np.where(good, scaled, 0).astype("uint8"))
        save(path, layers, profile, 0)
        with rio.open(path, "r+") as dst:
            dst.colorinterp = (rio.enums.ColorInterp.red, rio.enums.ColorInterp.green, rio.enums.ColorInterp.blue)
    return {"file": path.name, "item": source["id"], "utc": source["properties"]["datetime"],
            "role": "pre-event cartographic context only, independent 1.5-98.5 percentile DN stretch",
            "assets": {k:source["assets"][k]["href"] for k in ("red", "green", "blue")}}


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    host = "sentinel1euwestrtc.blob.core.windows.net"
    for key in ("NO_PROXY", "no_proxy"):
        os.environ[key] = ",".join(filter(None, (os.environ.get(key), host)))
    profile = grid()
    items = search(STAC, "sentinel-1-rtc", "2024-10-06", "2024-11-01")
    token = get("https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc")["token"]
    official = cache_json("activation.json", EMS)["results"][0]
    aoi = next(i for i in official["aois"] if int(i["number"]) == 1)
    first = next(i for i in aoi["products"] if i["type"] == "DEL" and not i["monitoring"])
    urls = [v for layer in first["layers"] for v in layer.values()
            if isinstance(v, str) and v.endswith("observedEventA_v1.json")]
    if len(urls) != 1:
        raise RuntimeError("Cannot identify authoritative EMS delineation layer")
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = [pool.submit(sar, items, date, 110, profile, token)
                for date in ("2024-10-08", "2024-10-20", "2024-11-01")]
        ref = pool.submit(cache_json, "ems-delineation.geojson", urls[0])
        rgb_job = pool.submit(optical, profile)
        dem = RAW / "terrain.tif"
        if not dem.exists():
            save(dem, read_window(DEM, profile, Resampling.bilinear), profile, np.nan)
        captures = [job.result() for job in jobs]
        ref.result()
        rgb = rgb_job.result()
    (RAW / "provenance.json").write_text(json.dumps({
        "bbox_lon_lat": BBOX, "crs": CRS, "resolution_m": RES, "sar": captures,
        "ems": {"activation": EMS, "layer": urls[0], "image": first["images"][0],
                "extent_wkt": first["extent"], "event_time": official["eventTime"]},
        "terrain": DEM, "image_context": rgb,
    }, indent=2))


if __name__ == "__main__":
    main()
