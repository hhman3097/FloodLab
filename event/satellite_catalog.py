"""Query image footprints. Catalog hits are not observations of water."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
from urllib.parse import urlencode

ENDPOINT = "https://stac.dataspace.copernicus.eu/v1/search"
COLLECTIONS = ("sentinel-1-grd", "sentinel-2-l2a")
# A research window for the two named targets, not an administrative boundary.
PUTIAN_WINDOW = (118.55, 25.16, 119.24, 25.67)


def query_catalog(place, from_date, to_date, utc_offset, bbox, download):
    if not isinstance(place, str) or not place.strip() or len(place) > 120:
        raise ValueError("请输入简短的研究区名称")
    if any(name in place.casefold() for name in ("莆田", "仙游", "putian", "xianyou")):
        bbox = PUTIAN_WINDOW
        place = "莆田—仙游"
        extent_source = "FloodLab 固定的莆田—仙游近似检索框，非行政边界"
    else:
        extent_source = "提供的检索框，非行政边界"
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or any(type(n) not in (float, int) or not math.isfinite(n) for n in bbox):
        raise ValueError("请输入已知的研究区坐标框 [西, 南, 东, 北]；不要猜测坐标")
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90 and e-w <= 12 and n-s <= 12):
        raise ValueError("研究区超出检索范围限制，请缩小到目标区域")
    start, end = date.fromisoformat(from_date), date.fromisoformat(to_date)
    if not 0 <= (end-start).days <= 60:
        raise ValueError("请选择不超过 61 天的检索范围")
    if type(utc_offset) is not int or not -720 <= utc_offset <= 840:
        raise ValueError("须提供研究区相对 UTC 的分钟数，例如中国为 480")
    tz = timezone(timedelta(minutes=utc_offset))
    first = datetime.combine(start,time.min,tz).astimezone(timezone.utc)
    stop = datetime.combine(end+timedelta(days=1),time.min,tz).astimezone(timezone.utc)
    last = stop-timedelta(microseconds=1)

    def fetch(collection):
        url = ENDPOINT+"?"+urlencode({"collections":collection,"bbox":",".join(map(str,bbox)),
                                      "datetime":f"{first.isoformat()}/{last.isoformat()}","limit":100})
        try:
            data = json.loads(download(url,12_000_000))
            if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"),list):
                raise ValueError("Unexpected catalog response")
            tiles = []
            for item in data["features"]:
                if item.get("collection")!=collection:
                    continue
                props = item["properties"];shape = item.get("geometry") or {}
                if shape.get("type") not in {"Polygon","MultiPolygon"}:
                    continue
                timestamp = datetime.fromisoformat(props["datetime"])
                if not first <= timestamp < stop:
                    continue
                tiles.append({"id":item["id"],"datetime_utc":timestamp.astimezone(timezone.utc).isoformat(),
                              "datetime_local":timestamp.astimezone(tz).isoformat(),
                              "orbit":props.get("sat:relative_orbit"),"orbit_state":props.get("sat:orbit_state"),
                              "cloud_pct_scene":props.get("eo:cloud_cover"),"geometry":shape})
            return {"collection":collection,"url":url,"status":"ok","items":tiles,
                    "truncated":any(l.get("rel")=="next" for l in data.get("links",[]))}
        except (OSError,ValueError,KeyError,TypeError) as exc:
            return {"collection":collection,"url":url,"status":"error","error":str(exc)[:180],"items":[]}

    with ThreadPoolExecutor(max_workers=2) as pool:
        records=list(pool.map(fetch,COLLECTIONS))
    return {"kind":"satellite_coverage","aoi":place,"bbox":list(bbox),"bbox_source":extent_source,
            "from":str(start),"to":str(end),"utc_offset_minutes":utc_offset,"catalogs":records,
            "retrieved_utc":datetime.now(timezone.utc).isoformat(),
            "remark":"场景足迹与检索框相交仅表示目录覆盖，不代表积水范围；整景云量不是研究区云量。未下载像元。"}


def write_coverage(folder: Path, result, put):
    features = []
    for catalog in result["catalogs"]:
        for tile in catalog["items"]:
            features.append({"type":"Feature","geometry":tile["geometry"],
                             "properties":{"collection":catalog["collection"],**{k:v for k,v in tile.items() if k!="geometry"}}})
    put(folder/"coverage.geojson",{"type":"FeatureCollection","features":features})
    put(folder/"results.json",result)
