import csv
import json
from collections import Counter

import numpy as np
import pytest
import rasterio
from affine import Affine

from agent import BENCHMARK, TRANSFER, RasterTools, check_task, decide_action
from floodlab import (DATA, OUT, ROOT, METHODS, NODATA, assess, matching_grid, otsu_mask,
                      read_image, read_label, read_split, scores, sha256)


def test_metrics_and_invalid_pixels():
    label = np.array([[1, 0, 1, 0, -1]])
    mask = np.array([[True, True, False, False, True]])
    result = assess(mask, label, label != -1, np.full((1, 5), .001))
    assert {k: result[k] for k in ("tp", "fp", "fn", "tn")} == dict(tp=1, fp=1, fn=1, tn=1)
    assert result["iou"] == pytest.approx(1 / 3)
    assert result["signed_error_km2"] == 0
    assert assess(mask, label, label < -2, np.ones((1, 5)))["eval_pred_km2"] is None
    assert scores(0, 0, 0, 10)["iou"] is None


def test_otsu_handles_empty_and_constant():
    array = np.zeros((2, 2, 2))
    for valid in (np.ones((2, 2), dtype=bool), np.zeros((2, 2), dtype=bool)):
        mask, threshold = otsu_mask(array, valid)
        assert not mask.any() and threshold is None


def test_all_test_products():
    items = read_split("test")
    assert len(items) == 90
    with (OUT / "test" / "per_chip.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 180
    indexed = {(r["chip"], r["method"]): r for r in rows}
    counts = Counter()
    omitted = set()
    for chip, image, label in items:
        _, valid, profile = read_image(DATA / image)
        reference, ref_valid = read_label(DATA / label, profile)
        joint = valid & ref_valid
        truth = reference[joint] == 1
        if not joint.any():
            omitted.add(chip)
        for m in METHODS:
            row = indexed[chip, m]
            with rasterio.open(ROOT / row["mask"]) as ds:
                pixels = ds.read(1)
                assert matching_grid(ds, profile)
                assert ds.nodata == NODATA
                assert np.array_equal(pixels == NODATA, ~valid)
                assert np.isin(pixels, (0, 1, NODATA)).all()
            pred = pixels[joint] == 1
            confusion = np.bincount(2 * truth + pred, minlength=4)
            for k, n in zip(("tn", "fp", "fn", "tp"), confusion):
                assert int(row[k]) == n
                counts[m, k] += n
    assert omitted == {"Ghana_83483", "Paraguay_34417"}
    result = json.loads((OUT / "test" / "summary.json").read_text())
    for row in result["groups"]:
        if row["event"] != "all":
            continue
        totals = {k: counts[row["method"], k] for k in ("tp", "fp", "fn", "tn")}
        assert all(row[k] == n for k, n in totals.items())
        assert row["iou"] == pytest.approx(scores(**totals)["iou"])


def test_data_checksums():
    manifest = json.loads((OUT / "inputs-test.json").read_text())
    assert manifest["chips"] == 90
    assert len(manifest["files"]) == 182
    for item in manifest["files"]:
        path = DATA / item["path"]
        assert path.stat().st_size == item["bytes"]
        assert sha256(path) == item["sha256"]


def test_agent_replays_recorded_assessment():
    truth = {i: (expected, target, tool, m) for i, _, expected, target, tool, m in BENCHMARK + TRANSFER}
    for name, size in [("benchmark.json", 28), ("benchmark-v1.json", 14), ("legacy-transfer.json", 14)]:
        log = json.loads((OUT / "agent" / name).read_text())
        assert log["size"] == size == len(log["records"])
        assert log["passed"] == sum(r["passed"] for r in log["records"])
        for row in log["records"]:
            if name != "benchmark.json":
                assert not (row["passed"] and row["answer"].get("result", {}).get("kind") == "compare"
                            and row["id"].startswith("B"))
                continue
            assert check_task(row["answer"], row["trace"], *truth[row["id"]]) == row["passed"]


@pytest.mark.parametrize("raw", ['{"action":"map"}', '{"action":[]}', '{"action":"fcn","area":999}'])
def test_agent_rejects_wrong_schema(raw):
    class NoCalls:
        def call(self, *args):
            pytest.fail("Invalid action executed")
    assert decide_action(lambda *_: raw, NoCalls(), "Ghana_313799")["answer"]["finish"] == "failed"


def test_agent_never_opens_labels():
    assert all(path.name.endswith("_S1Hand.tif") for path in RasterTools().images.values())


def test_grid_rejects_pixel_shift():
    from types import SimpleNamespace
    _, _, profile = read_image(DATA / read_split("test")[0][1])
    transform = profile["transform"] @ Affine.translation(1, 0)
    data = SimpleNamespace(shape=(512, 512), crs=profile["crs"], transform=transform)
    assert not matching_grid(data, profile)
