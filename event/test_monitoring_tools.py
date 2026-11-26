import json
from zipfile import ZipFile

import pytest

from ems_project import EventTools
from sar_project import CASE, cases, make_bundle
from transport_case import CLASSES, PROD, Transport


def test_sar_tool_rejects_wrong_scope_and_cross_chat_ids(tmp_path):
    first, other = tmp_path / "first", tmp_path / "other"
    first.mkdir()
    other.mkdir()
    tools = EventTools(first)
    assert tools.call("sar_inventory", {})["case"] == CASE
    with pytest.raises(RuntimeError, match="Valencia case"):
        tools.call("download_sar_pair", {"case": "other", "before": cases()["before_options"][0],
                                         "after": cases()["after_options"][0]})
    with pytest.raises(RuntimeError, match="this conversation"):
        EventTools(other).call("segment_sar_flood", {"download_id": "sar-earlier", "preset": "balanced"})
    assert tools.audit[-1]["response"]["status"] == "error"
    assert not (first / "inputs").exists()


def test_transport_scope_selects_official_towns_and_rejects_paths(tmp_path):
    t = Transport(tmp_path)
    inv = t.inventory()
    towns = {r["code"]: r for r in inv["towns"]}
    assert towns["46054"]["name"] == "Benetússer"
    assert towns["46186"]["coverage_pct_of_town"] == 100.0
    assert inv["product"] == PROD
    for args in [("../unsafe", ["46054"]), ("unknown_town", ["99999"])]:
        with pytest.raises(ValueError):
            t.screen(PROD, args[1], list(CLASSES), args[0])
    assert not any(tmp_path.iterdir())


def test_project_bundle_preserves_relative_asset_paths(tmp_path):
    target = tmp_path / "outputs" / "view"
    target.mkdir(parents=True)
    source = tmp_path / "inputs" / "capture"
    source.mkdir(parents=True)
    image = source / "image.tif"
    image.write_bytes(b"image")
    manifest = target / "results.json"
    manifest.write_text(json.dumps({"input": "capture"}))
    make_bundle(target, [image, manifest], tmp_path)
    with ZipFile(target / "project.zip") as bundle:
        assert bundle.testzip() is None
        assert sorted(bundle.namelist()) == ["inputs/capture/image.tif", "outputs/view/results.json"]
