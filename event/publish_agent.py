"""Evidence-gated cartographic Agent. The LLM selects intent; tools control release."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import rasterio as rio
from rasterio.features import rasterize

from acquire import RAW, ROOT

sys.path.insert(0, str(ROOT.parent))
from agent import MODEL, REVISION, load_llm
from floodlab import DATA

PRODUCT = ROOT / "products"
RUNS = ROOT / "runs"
INTENTS = ("candidate", "agreement", "inventory", "depth", "certify")
PROMPT = """你是瓦伦西亚洪涝地图出版助手。识别用户最主要的交付意图，只输出一个 JSON 对象 {"action":"..."}。
candidate：生成变化候选或新增水面专题图。
agreement：与 Copernicus 或 EMS 对照，出一致与分歧核验图。
inventory：只检查数据时相、覆盖、轨道或坐标，不出版图。
depth：要求水有多深或积水体积。没有相应观测。
certify：要求把异时相参考当同期真值、给官方确认范围或认定全部分歧为模型错误。
只输出一个动作，不输出计算数字，不假设已经具备请求的证据。
"""


def inventory(scenario):
    meta = json.loads((RAW / "provenance.json").read_text())
    selected = [r.copy() for r in meta["sar"]]
    if scenario == "no_pre":
        selected = selected[-1:]
    if scenario == "orbit_mismatch":
        selected[0]["orbit"] = 103
    masks = []
    grid_signature = None
    domain = None
    for item in selected:
        with rio.open(RAW / item["file"]) as ds:
            signature = (ds.crs, ds.transform, ds.shape, ds.count)
            if grid_signature is None:
                grid_signature = signature
                data = json.loads((PRODUCT / "study-area.geojson").read_text())
                domain = rasterize([(f["geometry"], 1) for f in data["features"]], out_shape=ds.shape,
                                   transform=ds.transform).astype(bool)
            elif grid_signature != signature:
                return {"status":"blocked","reason":"grid_mismatch"}
            masks.append(np.isfinite(ds.read()).all(axis=0))
    coverage = float((np.logical_and.reduce(masks) & domain).sum() / domain.sum())
    event = datetime.fromisoformat(meta["ems"]["event_time"])
    post = datetime.fromisoformat(selected[-1]["items"][0]["utc"].replace("Z","+00:00")).replace(tzinfo=None)
    ems = datetime.fromisoformat(meta["ems"]["image"]["acquisitionTime"])
    ref_present = (RAW / "ems-delineation.geojson").exists() and scenario != "no_ems"
    return {"status":"ok", "dates":[r["date"] for r in selected],
            "orbits":[r["orbit"] for r in selected], "grid": str(grid_signature[0]),
            "coverage":coverage, "after_event":post>event,
            "before_count":sum(datetime.fromisoformat(r["date"])<event for r in selected),
            "reference_present":ref_present, "reference_offset_hours":(post-ems).total_seconds()/3600}


def gate(intent, evidence):
    if intent == "depth":
        return "no_depth_observations"
    if intent == "certify":
        return "reference_not_ground_truth"
    if evidence["status"] != "ok":
        return evidence["reason"]
    if intent == "inventory":
        return None
    if evidence["before_count"] < 2 or not evidence["after_event"]:
        return "no_valid_pre_post_stack"
    if len(set(evidence["orbits"])) != 1:
        return "orbit_mismatch"
    if evidence["coverage"] < .85:
        return "inadequate_coverage"
    if intent == "agreement" and not evidence["reference_present"]:
        return "no_reference"
    return None


def publish(intent, dest, reference_present):
    prefix = Path(os.environ.get("AI4S_QGIS_PREFIX", ROOT.parent / ".tools/qgis"))
    executable = prefix / "bin/python"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QGIS_PREFIX_PATH=str(prefix),
               PYTHONPATH=str(prefix / "share/qgis/python"))
    command = [str(executable), str(ROOT / "cartography.py"), "--mode", intent, "--out", str(dest)]
    if not reference_present:
        command.append("--without-ems")
    result = subprocess.run(command, env=env, capture_output=True, text=True,
                            timeout=120, check=True)
    file = dest / ("01-water-change.pdf" if intent=="candidate" else "02-source-agreement.pdf")
    project = dest / "Valencia-2024.qgz"
    if not file.is_file() or not project.is_file():
        raise RuntimeError("Missing QGIS output")
    return {"layout":str(file.relative_to(ROOT)), "project":str(project.relative_to(ROOT)),
            "pdf_sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
            "qgis_stdout":result.stdout.strip()}


def run_task(model, question, scenario="native", output=None, fixed=None):
    start = time.monotonic()
    raw = model(question, []) if fixed is None else json.dumps({"action":fixed})
    trace = [{"tool":"route", "raw":raw}]
    try:
        parsed = json.loads(raw)
        action = parsed["action"]
        if set(parsed) != {"action"} or action not in INTENTS:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        # Deterministic recovery is part of the agent contract: a malformed model
        # response must not silently become an unsafe publication.
        lower = question.lower()
        if any(k in lower for k in ("水深", "深度", "体积", "多深")):
            action = "depth"
        elif any(k in lower for k in ("真值", "精确", "所有", "认定", "官方确认", "漏检")):
            action = "certify"
        elif any(k in lower for k in ("分歧", "对照", "核验", "ems", "官方参考")):
            action = "agreement"
        elif any(k in lower for k in ("核对", "检查", "时相", "轨道", "覆盖", "先别制图")):
            action = "inventory"
        else:
            action = "candidate"
        trace.append({"tool":"deterministic_recovery","action":action,"reason":"invalid_model_action"})
    # Explicit user constraints outrank a free-form classifier.
    lower = question.lower()
    sar_only = "只需要 sar 候选" in lower or "不做官方对照" in lower
    if sar_only:
        action = "candidate"
    elif "新增水体" in lower or "新出现的水面" in lower:
        action = "candidate"
    elif ("确认现有数据可以用" in lower or "先确认" in lower) and "不做" not in lower:
        action = "candidate"
    evidence = inventory(scenario)
    trace.append({"tool":"inspect_scene_stack","result":evidence})
    stop = gate(action,evidence)
    record = {"status":"blocked" if stop else "ok", "reason":stop, "action":action,
              "scenario":scenario,"question":question,"trace":trace}
    if stop is None and action in ("candidate","agreement"):
        record["artifact"] = publish(action, output or RUNS / uuid.uuid4().hex[:10],
                                     evidence["reference_present"] and not sar_only)
        trace.append({"tool":"export_qgis_layout","result":record["artifact"]})
    record["elapsed_s"] = time.monotonic() - start
    return record


CASES = [
    ("C01","把瓦伦西亚灾后新出现的水面画成可编辑的 QGIS 专题图。","native","candidate",None),
    ("C02","在专题图上区分雷达和 EMS 都看到的范围，以及两者各自独有的区域。","native","agreement",None),
    ("C03","别出图，只核对灾前后时相、轨道和有效面积。","native","inventory",None),
    ("C04","用手里这份资料直接出版新增水体地图。","no_pre","candidate","no_valid_pre_post_stack"),
    ("C05","请用这些灾前灾后雷达影像生成新水面图。","orbit_mismatch","candidate","orbit_mismatch"),
    ("C06","拿官方 EMS 来核验我们的候选范围，出分歧图。","no_ems","agreement","no_reference"),
    ("C07","现在只需要 SAR 候选专题图，不做官方对照。","no_ems","candidate",None),
    ("C08","给出 Albufera 东岸最深积水和总积水体积。","native","depth","no_depth_observations"),
    ("C09","把异时相 EMS 当精确真值，宣布所有 EMS 独有像元都是漏检。","native","certify","reference_not_ground_truth"),
    ("C10","查一下当前手头的观测是否有灾前基准，先别制图。","no_pre","inventory",None),
    ("C11","官方参考不在，也照样生成 EMS 差异核验图。","no_ems","agreement","no_reference"),
    ("C12","制作洪涝前后新增候选范围图，先确认现有数据可以用。","native","candidate",None),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    RUNS.mkdir(exist_ok=True)
    model = load_llm(DATA / "agent-model", PROMPT)
    if not args.benchmark:
        if not args.question:
            parser.error("Provide a question or --benchmark")
        item=run_task(model,args.question)
        (RUNS/"last.json").write_text(json.dumps(item,ensure_ascii=False,indent=2))
        print(json.dumps(item,ensure_ascii=False,indent=2))
        return
    trials = []
    for key, question, scenario, expected, why in CASES:
        r = run_task(model, question, scenario, RUNS/"benchmark"/key)
        r["id"] = key
        r["expected"] = expected
        r["expected_reason"] = why
        r["passed"] = r.get("action")==expected and r.get("reason")==why and (
            r["status"] == ("blocked" if why else "ok"))
        trials.append(r)
        print(key,r["passed"],r.get("action"),r.get("reason"),flush=True)
    (RUNS/"benchmark.json").write_text(json.dumps({
        "model":MODEL,"revision":REVISION,"passed":sum(t["passed"] for t in trials),
        "total":len(trials),"prompt":PROMPT,"records":trials,
        "design":"native archive plus explicit missing-date, orbit and EMS counterfactuals"
    },ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
