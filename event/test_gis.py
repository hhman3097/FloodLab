"""Focused invariants for the GIS worker, independent of language-model success."""
import json
from pathlib import Path

import pytest

from gis_client import GISClient

ROOT = Path(__file__).resolve().parent


def test_historical_road_snapshot():
    provenance = json.loads((ROOT / "inputs/assets/provenance.json").read_text())
    roads = json.loads((ROOT / "inputs/assets/roads-20241028.geojson").read_text())["features"]
    assert len({r["properties"]["osm_id"] for r in roads}) == len(roads)
    assert all(r["properties"]["edited_utc"] <= provenance["snapshot_utc"] for r in roads)
    assert len(roads) == provenance["features"]


def test_dependency_invalidation_and_crs_guards(tmp_path):
    with GISClient(tmp_path) as client:
        def call(tool, **kwargs):
            result = client.call(tool, **kwargs)
            assert result["status"] == "ok", result
            return result["result"]

        bad = client.call("process", algorithm="native:clip",
                          params={"INPUT": {"layer": "roads"}, "OVERLAY": {"layer": "flood"}},
                          output_id="wrong_crs")
        assert bad["status"] == "error" and "CRS mismatch" in bad["message"]
        bad = client.call("process", algorithm="native:creategrid",
                          params={"TYPE": 2, "EXTENT": {"layer": "aoi"}, "CRS": "EPSG:4326",
                                  "HSPACING": 1, "VSPACING": 1}, output_id="degrees_grid")
        assert bad["status"] == "error" and "metre CRS" in bad["message"]
        call("set_parameters", values={"size": 1000})
        call("process", algorithm="native:reprojectlayer",
             params={"INPUT": {"layer": "aoi"}, "TARGET_CRS": "EPSG:32630"}, output_id="aoi_m")
        call("process", algorithm="native:creategrid",
             params={"TYPE": 2, "EXTENT": {"layer": "aoi_m"}, "CRS": "EPSG:32630",
                     "HSPACING": {"parameter": "size"}, "VSPACING": {"parameter": "size"}},
             output_id="grid")
        call("process", algorithm="native:fieldcalculator",
             params={"INPUT": {"layer": "grid"}, "FIELD_NAME": "value", "FIELD_TYPE": 0,
                     "FORMULA": "1"}, output_id="derived")
        changed = call("set_parameters", values={"size": 2000})
        assert set(changed["invalidated"]) == {"grid", "derived"}
        call("inspect", layer_id="aoi_m")
        stale = client.call("inspect", layer_id="derived")
        assert stale["status"] == "error" and "Stale dependency" in stale["message"]
        unchanged = call("set_parameters", values={"size": 2000})
        assert unchanged["changed"] == []
    with GISClient(tmp_path, resume=True) as restored:
        result = restored.call("state")
        assert result["result"]["parameters"]["size"] == 2000
        assert result["result"]["layers"]["derived"]["stale"]
        assert restored.call("inspect", layer_id="aoi_m")["status"] == "ok"
        assert restored.call("inspect", layer_id="derived")["status"] == "error"


def test_generic_condition_can_read_old_outputs(tmp_path):
    """The baseline shares tools but has no automatic stale-result guard."""
    with GISClient(tmp_path, tracked=False) as client:
        client.call("set_parameters", values={"filter": "TRUE"})
        result = client.call("process", algorithm="native:extractbyexpression",
                            params={"INPUT": {"layer": "roads"},
                                    "EXPRESSION": {"parameter": "filter"}}, output_id="selected")
        assert result["status"] == "ok"
        client.call("set_parameters", values={"filter": '"highway" = \'primary\''})
        assert client.call("inspect", layer_id="selected")["status"] == "ok"
        state = client.call("state")["result"]
        assert "stale" not in state["layers"]["selected"]
