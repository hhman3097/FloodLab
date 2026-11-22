import hashlib
import json
import pytest
import ems_project


def test_provenance_preserves_first_download_and_unregistered_files(tmp_path,monkeypatch):
    tools=ems_project.EventTools(tmp_path)
    key="EMSR933:2962";tools.index["catalog"][key]={"source":"fixture"}
    raw=json.dumps({"type":"FeatureCollection","features":[{"geometry":{"type":"Polygon","coordinates":[[[0,0],[1,0],[1,1],[0,0]]]}}]}).encode()
    downloads=[]
    monkeypatch.setattr(ems_project,"download",lambda *a:downloads.append(a) or raw)
    folder=tmp_path/"inputs"/key.replace(":","-");folder.mkdir(parents=True)
    file=folder/"observed.geojson";file.write_bytes(b"unregistered")
    with pytest.raises(ValueError):tools.call("get_footprint",{"key":key})
    assert file.read_bytes()==b"unregistered" and key not in tools.index["products"]
    file.unlink()
    first=tools.call("get_footprint",{"key":key})
    second=tools.call("get_footprint",{"key":key})
    assert len(downloads)==2 and first["retrieved_utc"]==second["retrieved_utc"]
    assert second["sha256"]==hashlib.sha256(file.read_bytes()).hexdigest()
