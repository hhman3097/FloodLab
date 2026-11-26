import json
from urllib.parse import parse_qs, urlsplit

import pytest

from ems_project import EventTools
from satellite_catalog import PUTIAN_WINDOW, query_catalog


def feature(day, collection):
    return {"id":day,"collection":collection,
            "properties":{"datetime":day,"eo:cloud_cover":100},
            "geometry":{"type":"Polygon","coordinates":[[[118,25],[119,25],[119,26],[118,25]]]}}


def test_local_days_cloud_metadata_and_distinct_provider_failure():
    def get(url,_):
        args=parse_qs(urlsplit(url).query)
        assert args["bbox"][0]==",".join(map(str,PUTIAN_WINDOW))
        assert args["datetime"][0]=="2026-09-02T16:00:00+00:00/2026-09-06T15:59:59.999999+00:00"
        collection=args["collections"][0]
        if collection=="sentinel-1-grd":raise TimeoutError("fixture")
        return json.dumps({"type":"FeatureCollection","features":[feature(d,collection) for d in
                           ("2026-09-02T15:59:59Z","2026-09-02T16:00:00Z","2026-09-06T16:00:00Z")],
                           "links":[{"rel":"next"}]})

    result=query_catalog("莆田","2026-09-03","2026-09-06",480,[118,25,119,26],get)
    radar,optical=result["catalogs"]
    assert radar["status"]=="error"
    assert optical["status"]=="ok" and optical["truncated"]
    assert [(t["datetime_local"],t["cloud_pct_scene"]) for t in optical["items"]]==[("2026-09-03T00:00:00+08:00",100)]


def test_empty_coverage_is_downloadable_not_a_flood_polygon(tmp_path,monkeypatch):
    import ems_project
    monkeypatch.setattr(ems_project,"download",lambda *_:b'{"type":"FeatureCollection","features":[]}')
    tools=EventTools(tmp_path)
    result=tools.call("search_satellite",{"place":"仙游","from_date":"2026-09-03",
                                        "to_date":"2026-09-06","utc_offset_minutes":480})
    assert result["kind"]=="satellite_coverage" and "total_km2" not in result
    assert all(c["status"]=="ok" and c["count"]==0 for c in result["catalogs"])
    assert json.loads((tmp_path/result["folder"]/"coverage.geojson").read_text())["features"]==[]
    assert EventTools(tmp_path).index["outputs"]==[result]


@pytest.mark.parametrize("bbox", [None,[119,25,118,26],[0,-80,12,85],[0,"1",2,3]])
def test_unknown_or_invalid_extent_is_not_guessed(bbox):
    with pytest.raises(ValueError):
        query_catalog("未定位地点","2026-09-03","2026-09-06",480,bbox,lambda *_:pytest.fail("Unexpected query"))
