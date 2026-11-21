import argparse
import csv
import hashlib
import json
import platform
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import rasterio
from pyproj import CRS
from skimage.filters import threshold_otsu

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "outputs"
BUCKET = "https://storage.googleapis.com/sen1floods11/v1.1"
HAND = "data/flood_events/HandLabeled"
WEIGHT_FILE = "5e4_handlabeled_0_8_0.43163052201271057.cp"
WEIGHT_SHA256 = "0972a4420e224419898d50b037ff77cc902e0231d73c6dcd2e330f0d05cc11c1"
NODATA = 255
METHODS = ("otsu", "fcn")
SPLITS = {"test": "test", "validation": "valid", "train": "train"}


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def fetch(relative, expected_size=None):
    destination = DATA / relative
    if destination.is_file() and (expected_size is None or destination.stat().st_size == expected_size):
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(3):
        try:
            import shutil
            with urlopen(f"{BUCKET}/{relative}", timeout=25) as response, temp.open("wb") as stream:
                shutil.copyfileobj(response, stream)
                size = expected_size or response.headers.get("Content-Length")
            if size is not None and temp.stat().st_size != int(size):
                raise OSError(f"Incomplete download: {relative}")
            temp.replace(destination)
            return destination
        except OSError:
            temp.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def split_relative(split):
    return f"splits/flood_handlabeled/flood_{SPLITS[split]}_data.csv"


def read_split(split, limit=None, download=False):
    relative = split_relative(split)
    path = fetch(relative) if download else DATA / relative
    with path.open(newline="", encoding="utf-8") as stream:
        pairs = list(csv.reader(stream))
    result = []
    for image, label in pairs:
        key = image.removesuffix("_S1Hand.tif")
        if not re.fullmatch(r"[A-Za-z-]+_\d+", key) or label != f"{key}_LabelHand.tif":
            raise ValueError(f"Unexpected official split row: {image}, {label}")
        result.append((key, f"{HAND}/S1Hand/{image}", f"{HAND}/LabelHand/{label}"))
    if len({row[0] for row in result}) != len(result):
        raise ValueError("Duplicate chip in split")
    return result[:limit]


def acquire(split, limit):
    items = read_split(split, limit, download=True)
    jobs = [(f"checkpoints/{WEIGHT_FILE}", 131811359)]
    jobs.extend((path, None) for _, image, label in items for path in (image, label))
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda job: fetch(*job), jobs))
    paths.append(DATA / split_relative(split))
    dump(OUT / f"inputs-{split}.json", {
        "split": split, "chips": len(items), "bucket": BUCKET,
        "files": [{"path": str(p.relative_to(DATA)), "sha256": sha256(p),
                   "bytes": p.stat().st_size} for p in paths],
    })
    print(f"Acquired {len(items)} {split} chips and official checkpoint", flush=True)


def read_image(path):
    with rasterio.open(path) as ds:
        if ds.count != 2 or ds.crs is None or ds.shape != (512, 512):
            raise ValueError("Expected georeferenced 512 x 512 VV/VH pair")
        array = ds.read()
        if not np.issubdtype(array.dtype, np.floating):
            raise ValueError("Expected floating-point backscatter in dB")
        valid = np.all(ds.read_masks() > 0, axis=0) & np.isfinite(array).all(axis=0)
        return array, valid, ds.profile.copy()


def matching_grid(ds, profile):
    return (ds.shape == (profile["height"], profile["width"])
            and ds.crs == profile["crs"]
            and ds.transform.almost_equals(profile["transform"], precision=1e-12))


def read_label(path, profile):
    with rasterio.open(path) as ds:
        if ds.count != 1 or not matching_grid(ds, profile):
            raise ValueError("Label grid differs from the image grid")
        label = ds.read(1)
        if not np.isin(label, [-1, 0, 1]).all():
            raise ValueError(f"Unexpected label values: {np.unique(label)}")
        return label, (ds.read_masks(1) > 0) & (label != -1)


def area_grid(profile):
    crs = CRS.from_user_input(profile["crs"])
    t = profile["transform"]
    if crs.to_epsg() != 4326 or t.b or t.d or not (t.a > 0 and t.e < 0):
        raise ValueError("Area tool expects the official north-up WGS84 chip grid")
    # All columns at a given latitude have the same ellipsoidal pixel area.
    geod = crs.get_geod()
    areas = []
    for row in range(profile["height"]):
        y1, y2 = t.f + row * t.e, t.f + (row + 1) * t.e
        area, _ = geod.polygon_area_perimeter(
            [t.c, t.c + t.a, t.c + t.a, t.c], [y1, y1, y2, y2])
        areas.append(abs(area) / 1_000_000)
    return np.broadcast_to(np.array(areas)[:, None], (profile["height"], profile["width"]))


def otsu_mask(array, valid):
    vh = array[1]
    sample = vh[valid]
    if not sample.size or np.min(sample) == np.max(sample):
        return np.zeros(valid.shape, dtype=bool), None
    threshold = float(threshold_otsu(sample, nbins=256))
    return (vh <= threshold) & valid, threshold


class FCN:
    def __init__(self, device="cpu"):
        import torch
        from torch import nn
        from torchvision.models.segmentation import fcn_resnet50

        self.torch = torch
        self.device = device
        if device == "cpu":
            torch.set_num_threads(4)
        checkpoint = DATA / "checkpoints" / WEIGHT_FILE
        if sha256(checkpoint) != WEIGHT_SHA256:
            raise ValueError("Checkpoint digest differs from the recorded public artifact")
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        aux = any(key.startswith("aux_classifier.") for key in state)
        model = fcn_resnet50(weights=None, weights_backbone=None, num_classes=2, aux_loss=aux)
        model.backbone.conv1 = nn.Conv2d(2, 64, 7, stride=2, padding=3, bias=False)

        def convert(layer):
            for key, child in list(layer.named_children()):
                if isinstance(child, nn.BatchNorm2d):
                    setattr(layer, key, nn.GroupNorm(16, child.num_features, eps=child.eps))
                else:
                    convert(child)

        convert(model)
        model.load_state_dict(state, strict=True)
        self.model = model.eval().to(device)

    def predict(self, array, valid):
        if not valid.any():
            return np.zeros(valid.shape, dtype=bool)
        torch = self.torch
        # Match the public notebook's dB normalization and four 256 px inference windows.
        x = np.where(valid[None], array, 0)
        x = ((np.clip(x, -50, 1) + 50) / 51 - np.array([.6851, .5235])[:, None, None])
        x = (x / np.array([.0820, .1102])[:, None, None]).astype("float32")
        quadrants = [(r, c) for r in (0, 256) for c in (0, 256)]
        batch = np.stack([x[:, r:r+256, c:c+256] for r, c in quadrants])
        with torch.inference_mode():
            masks = self.model(torch.from_numpy(batch).to(self.device))["out"].argmax(1).cpu().numpy()
        out = np.zeros(valid.shape, dtype=bool)
        for (r, c), mask in zip(quadrants, masks):
            out[r:r+256, c:c+256] = mask == 1
        return out & valid


def save_mask(path, mask, valid, profile):
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(profile, driver="GTiff", count=1, dtype="uint8", nodata=NODATA,
                   compress="deflate", interleave="band")
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(np.where(valid, mask, NODATA).astype("uint8"), 1)
        ds.update_tags(meaning="Observed water, not newly inundated extent", classes="0,1,255")


def scores(tp, fp, fn, tn):
    def div(a, b):
        return a / b if b else None
    return dict(iou=div(tp, tp + fp + fn), precision=div(tp, tp + fp),
                recall=div(tp, tp + fn), f1=div(2 * tp, 2 * tp + fp + fn))


def assess(mask, label, valid, areas):
    truth = label == 1
    tp, fp, fn, tn = (int((x & valid).sum()) for x in (
        mask & truth, mask & ~truth, ~mask & truth, ~mask & ~truth))
    if not valid.any():
        return dict(tp=tp, fp=fp, fn=fn, tn=tn, **scores(tp, fp, fn, tn),
                    eval_pred_km2=None, eval_reference_km2=None, signed_error_km2=None)
    pred_area = float(areas[mask & valid].sum())
    ref_area = float(areas[truth & valid].sum())
    return dict(tp=tp, fp=fp, fn=fn, tn=tn, **scores(tp, fp, fn, tn),
                eval_pred_km2=pred_area, eval_reference_km2=ref_area,
                signed_error_km2=pred_area - ref_area)


def run(split, limit, device):
    import torch
    import torchvision

    items = read_split(split, limit)
    model = FCN(device)
    run_root = OUT / (split if limit is None else f"{split}-pilot")
    rows = []
    started = time.perf_counter()
    for index, (chip, image_path, label_path) in enumerate(items, 1):
        image, valid, profile = read_image(DATA / image_path)
        areas = area_grid(profile)
        a = time.perf_counter()
        otsu, threshold = otsu_mask(image, valid)
        b = time.perf_counter()
        fcn = model.predict(image, valid)
        c = time.perf_counter()
        # The reference is opened only after both predictions have been made.
        label, label_valid = read_label(DATA / label_path, profile)
        for method, mask, elapsed in [("otsu", otsu, b-a), ("fcn", fcn, c-b)]:
            path = run_root / "masks" / method / f"{chip}.tif"
            save_mask(path, mask, valid, profile)
            rows.append(dict(chip=chip, event=chip.rsplit("_", 1)[0], method=method,
                             mask=str(path.relative_to(ROOT)), seconds=elapsed,
                             threshold_db=threshold if method == "otsu" else None,
                             image_valid_pixels=int(valid.sum()),
                             observed_water_km2=float(areas[mask & valid].sum()),
                             **assess(mask, label, valid & label_valid, areas)))
        if index == 1 or index % 10 == 0 or index == len(items):
            print(f"{index}/{len(items)} {chip}: FCN {c-b:.2f}s", flush=True)
    with (run_root / "per_chip.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups = defaultdict(list)
    for row in rows:
        for event in ("all", row["event"]):
            groups[(event, row["method"])].append(row)
    aggregate = []
    for (event, method), group in sorted(groups.items()):
        counts = {k: sum(r[k] for r in group) for k in ("tp", "fp", "fn", "tn")}
        chip_iou = [r["iou"] for r in group if r["iou"] is not None]
        errors = [r["signed_error_km2"] for r in group if r["signed_error_km2"] is not None]
        aggregate.append(dict(event=event, method=method, chips=len(group), **counts,
                              **scores(**counts),
                              evaluated_chips=len(errors),
                              no_observation_chips=sum(r["image_valid_pixels"] == 0 for r in group),
                              mean_chip_iou=float(np.mean(chip_iou)) if chip_iou else None,
                              defined_chip_iou=len(chip_iou),
                              mean_absolute_area_error_km2=float(np.mean(np.abs(errors))) if errors else None,
                              mean_signed_area_error_km2=float(np.mean(errors)) if errors else None,
                              median_inference_seconds=float(np.median([r["seconds"] for r in group]))))
    dump(run_root / "summary.json", {
        "split": split, "chips": len(items), "device": device, "runtime": platform.platform(),
        "torch": torch.__version__, "torchvision": torchvision.__version__,
        "checkpoint": WEIGHT_FILE, "checkpoint_sha256": sha256(DATA / "checkpoints" / WEIGHT_FILE),
        "elapsed_seconds": time.perf_counter() - started, "groups": aggregate,
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["fetch", "run"])
    parser.add_argument("--split", choices=["test", "validation"], default="test")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--device", choices=["cpu", "mps", "cuda"], default="cpu")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.action == "fetch":
        acquire(args.split, args.limit)
    else:
        run(args.split, args.limit, args.device)


if __name__ == "__main__":
    main()
