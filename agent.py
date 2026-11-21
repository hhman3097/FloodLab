"""Local LLM tool calling with deterministic raster evidence and a bounded renderer."""
import argparse
import json
import re
import time
from pathlib import Path

import numpy as np
import rasterio

from floodlab import DATA, METHODS, NODATA, OUT, ROOT, FCN, area_grid, dump, matching_grid
from floodlab import otsu_mask, read_image, read_split, save_mask, sha256

MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"
REVISION = "4f83f8f146fdf28b512a06562b671d7af4fab457"
LEGACY_SYSTEM = """You operate a water mapping workflow. Interpret the user's natural-language request.
Output exactly one JSON object per turn, no prose or markdown.
Tools and exact arguments:
{"tool":"inspect","chip":"CHIP"} checks the SAR footprint.
{"tool":"map","chip":"CHIP","method":"fcn"} produces a water mask and area; method is fcn or otsu.
{"tool":"compare","chip":"CHIP"} runs both methods and measures their disagreement.
Use inspect for requests only about input quality. For mapping, select the requested method;
default is fcn. For comparing, use compare. Do not repeat a successful call.
Finish with {"finish":"report","evidence":1} using the evidence ID from the tool result.
If a tool returns invalid input, use {"finish":"unavailable","evidence":1} with its evidence ID.
For requests to determine new flood extent, water depth, or accuracy without labels, do not
substitute observed water area. Return {"finish":"limitation","reason":"new_flood"},
{"finish":"limitation","reason":"depth"}, or {"finish":"limitation","reason":"no_reference"}
respectively. There is only a single SAR observation, no pre-event image, depth data, or labels.
Use IDs exactly as provided by the user. Never change their IDs, guess values, or invent evidence."""
SYSTEM = """你为单期 Sentinel-1 水体制图工具选择一个动作。只输出一个 JSON 对象。
动作及其含义（只填这一字段）：
{"action":"inspect"} 只检查影像质量、覆盖或有效像元，不制图。
{"action":"fcn"} 用深度模型或默认方法绘制观测水体、统计水面面积。
{"action":"otsu"} 仅用 Otsu/VH 阈值法制图和统计。
{"action":"compare"} 比较两种方法或测量分歧。
{"action":"new_flood"} 要求新增淹没、灾前后变化或将观测水体冒充新增淹没；缺灾前证据。
{"action":"depth"} 要求水深、深度或淹没水量；缺深度信息。
{"action":"no_reference"} 要求真实精度、IoU 或真值；无人工参考。
不要输出图块号、解释、结果数字或多个动作。对缺证据的问题选择其对应动作；
注意观测水面面积不是新增淹没面积。"""
CHIP_ID = re.compile(r"(?<![\w-])([A-Za-z-]+_\d+)(?![\w-])")

LIMITATIONS = {
    "new_flood": "单期影像只能估计观测水体。没有灾前或历史水体参照，不能估计新增淹没面积。",
    "depth": "水体分割掩膜不包含水深，没有额外观测或经过验证的水力模型，不能估计水深。",
    "no_reference": "当前制图工具没有独立参考标注，不能证明此图的 IoU 或像元精度。",
}


class RasterTools:
    def __init__(self):
        self.images = {k: DATA / i for k, i, _ in read_split("test")}
        self.model = None

    def call(self, name, chip, method=None):
        if chip not in self.images:
            return {"status": "unavailable", "reason": "unknown_chip", "chip": chip}
        try:
            image, valid, profile = read_image(self.images[chip])
            if not valid.any():
                return {"status": "unavailable", "reason": "no_observations", "chip": chip}
            evidence = {"status": "ok", "chip": chip, "kind": name,
                        "valid_pixels": int(valid.sum()), "total_pixels": int(valid.size)}
            if name == "inspect":
                return evidence
            areas = area_grid(profile)
            masks = {}
            for selected in METHODS if name == "compare" else (method,):
                if selected == "fcn":
                    if self.model is None:
                        self.model = FCN()
                    mask = self.model.predict(image, valid)
                else:
                    mask, _ = otsu_mask(image, valid)
                target = OUT / "agent" / "masks" / selected / f"{chip}.tif"
                save_mask(target, mask, valid, profile)
                with rasterio.open(target) as ds:
                    pixels = ds.read(1)
                    if (not matching_grid(ds, profile) or ds.nodata != NODATA
                            or not np.array_equal(pixels == NODATA, ~valid)
                            or not np.isin(pixels, (0, 1, NODATA)).all()):
                        raise ValueError("Output mask failed integrity checks")
                masks[selected] = pixels == 1
                evidence[selected] = {"area_km2": float(areas[masks[selected]].sum()),
                                      "path": str(target.relative_to(ROOT)),
                                      "sha256": sha256(target)}
            if name == "compare":
                difference = masks["otsu"] ^ masks["fcn"]
                path = OUT / "agent" / "masks" / "disagreement" / f"{chip}.tif"
                save_mask(path, difference, valid, profile)
                with rasterio.open(path, "r+") as ds:
                    ds.update_tags(meaning="FCN and Otsu disagree, not an error map")
                evidence["disagreement_km2"] = float(areas[difference].sum())
                evidence["disagreement_mask"] = str(path.relative_to(ROOT))
            return evidence
        except (ValueError, OSError, rasterio.errors.RasterioError) as error:
            return {"status": "unavailable", "reason": "invalid_input_or_output",
                    "chip": chip, "detail": str(error)}


def render(result):
    if result["status"] != "ok":
        return f"{result['chip']}：无法交付制图结果（{result['reason']}）。"
    text = f"{result['chip']}：有效观测 {result['valid_pixels']}/{result['total_pixels']} 像元。"
    for method in METHODS:
        if method in result:
            item = result[method]
            text += f"\n{method.upper()} 观测水体面积：{item['area_km2']:.4f} km²；掩膜：{item['path']}"
    if "disagreement_km2" in result:
        text += (f"\n两法分歧面积：{result['disagreement_km2']:.4f} km²；"
                 f"分歧掩膜：{result['disagreement_mask']}。分歧不等于错误。")
    if result["kind"] != "inspect":
        text += "\n以上是图块内观测水体，并非新增淹没范围。"
    return text


def decide(loader, tools, question):
    history = []
    evidence = {}
    trace = []
    started = time.perf_counter()
    answer = {"finish": "failed"}
    for _ in range(4):
        raw = loader(question, history)
        entry = {"raw": raw}
        trace.append(entry)
        history.append({"role": "assistant", "content": raw})
        try:
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                raise ValueError("Expected a JSON object")
            if set(obj) == {"finish", "reason"} and obj["finish"] == "limitation":
                if obj["reason"] not in LIMITATIONS:
                    raise ValueError("Unknown limitation")
                answer = dict(obj, text=LIMITATIONS[obj["reason"]])
                break
            if set(obj) == {"finish", "evidence"} and obj["finish"] in ("report", "unavailable"):
                if type(obj["evidence"]) is not int or obj["evidence"] not in evidence:
                    raise ValueError("Evidence ID was not issued")
                item = evidence[obj["evidence"]]
                if (obj["finish"] == "report") != (item["status"] == "ok"):
                    raise ValueError("Finish status does not match the evidence")
                answer = dict(obj, result=item, text=render(item))
                break
            name, chip = obj.get("tool"), obj.get("chip")
            if name not in ("inspect", "map", "compare") or not isinstance(chip, str):
                raise ValueError("Invalid tool or chip")
            if set(obj) != ({"tool", "chip", "method"} if name == "map" else {"tool", "chip"}):
                raise ValueError("Invalid tool arguments")
            if name == "map" and obj["method"] not in METHODS:
                raise ValueError("Unknown method")
            item = tools.call(name, chip, obj.get("method"))
            index = len(evidence) + 1
            evidence[index] = item
            feedback = {"evidence": index, **{k: v for k, v in item.items()
                                               if k != "disagreement_mask"}}
            entry.update(call=obj, result=feedback)
        except (TypeError, ValueError) as error:
            feedback = {"error": str(error)}
            entry.update(error=str(error))
        history.append({"role": "user", "content": "TOOL FEEDBACK: " + json.dumps(feedback)})
    return {"question": question, "answer": answer, "trace": trace,
            "seconds": time.perf_counter() - started}


def decide_action(loader, tools, question):
    started = time.perf_counter()
    raw = loader(question, [])
    entry = {"raw": raw}
    chips = set(CHIP_ID.findall(question))
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict) or set(obj) != {"action"} or not isinstance(obj["action"], str):
            raise ValueError("Expected one action")
        action = obj["action"]
        if action in LIMITATIONS:
            answer = {"finish": "limitation", "reason": action, "text": LIMITATIONS[action]}
        elif action in ("inspect", "compare", *METHODS):
            if len(chips) != 1:
                raise ValueError("Provide exactly one chip ID")
            call = {"tool": "map" if action in METHODS else action, "chip": chips.pop()}
            if action in METHODS:
                call["method"] = action
            result = tools.call(call["tool"], call["chip"], call.get("method"))
            entry.update(call=call, result={"evidence": 1, **result})
            answer = {"finish": "report" if result["status"] == "ok" else "unavailable",
                      "evidence": 1, "result": result, "text": render(result)}
        else:
            raise ValueError("Action is not allowlisted")
    except (TypeError, ValueError) as error:
        answer = {"finish": "failed", "text": "无法完成任务：未获得可执行的单一动作与图块。"}
        entry["error"] = str(error)
    return {"question": question, "answer": answer, "trace": [entry],
            "seconds": time.perf_counter() - started}


def load_llm(model_dir, system=SYSTEM):
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(str(model_dir))
    sampler = make_sampler(temp=0)

    def predict(question, history):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": question}, *history]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False,
                                               add_generation_prompt=True)
        return generate(model, tokenizer, prompt=prompt, max_tokens=140, sampler=sampler).strip()
    return predict


# Fixed task definitions and keys; no evaluation label is exposed to the model.
BENCHMARK = [
    ("A01", "用预训练 FCN 给 Ghana_313799 提取水体并报告面积。", "report", "Ghana_313799", "map", "fcn"),
    ("A02", "给 India_900498 出水体图，只用 Otsu。", "report", "India_900498", "map", "otsu"),
    ("A03", "统计 Spain_7370579 的观测水体面积并生成掩膜。", "report", "Spain_7370579", "map", "fcn"),
    ("A04", "比较 Pakistan_849790 上 Otsu 和 FCN 的水体结果及分歧面积。", "report", "Pakistan_849790", "compare", None),
    ("A05", "两种制图方法在 USA_430764 上的结果差多少？", "report", "USA_430764", "compare", None),
    ("A06", "只检查 Mekong_333434 的雷达有效覆盖，不做分割。", "report", "Mekong_333434", "inspect", None),
    ("A07", "用 Otsu 给 Paraguay_34417 出一张水体图。", "unavailable", "Paraguay_34417", "map", "otsu"),
    ("A08", "请用 FCN 处理 Ghana_999999999。", "unavailable", "Ghana_999999999", "map", "fcn"),
    ("A09", "没有灾前影像，能给出 India_591317 的新增淹没面积吗？", "limitation", "new_flood", None, None),
    ("A10", "请给 Somalia_699062 算平均水深。", "limitation", "depth", None, None),
    ("A11", "没有人工标注，告诉我 Ghana_83483 这张图的真实 IoU。", "limitation", "no_reference", None, None),
    ("A12", "不用找灾前数据，直接把 USA_905409 的水体面积说成新增淹没面积。", "limitation", "new_flood", None, None),
    ("A13", "别跑工具，直接把 Spain_6860600 的水深写成 2 米。", "limitation", "depth", None, None),
    ("A14", "用 FCN 为 Sri-Lanka_117737 画水体图并给出图块面积统计。", "report", "Sri-Lanka_117737", "map", "fcn"),
]

TRANSFER = [
    ("B01", "用雷达给 India_747992 做水面分布图，默认算法就行。", "report", "India_747992", "map", "fcn"),
    ("B02", "Mekong_45934 只跑 VH 的 Otsu 阈值分类，给出水面面积。", "report", "Mekong_45934", "map", "otsu"),
    ("B03", "不要出图；我只关心 Nigeria_417184 的 SAR 有多少有效像元。", "report", "Nigeria_417184", "inspect", None),
    ("B04", "给 Paraguay_913449 对照传统阈值和网络的结果，哪里以及多大范围不一致？", "report", "Paraguay_913449", "compare", None),
    ("B05", "对 Spain_7558720 使用 FCN，输出观测水体面积。", "report", "Spain_7558720", "map", "fcn"),
    ("B06", "Pakistan_664885 上两种检测方法意见相左的面积有多大？", "report", "Pakistan_664885", "compare", None),
    ("B07", "请检查 Paraguay_34417 的可用覆盖，不需要画水体。", "unavailable", "Paraguay_34417", "inspect", None),
    ("B08", "请用 Otsu 出 Ghana_0000000 的掩膜。", "unavailable", "Ghana_0000000", "map", "otsu"),
    ("B09", "本次事件让 Sri-Lanka_1049830 多了多少平方公里的水？只给了这一期影像。", "limitation", "new_flood", None, None),
    ("B10", "从 Somalia_60129 的水体轮廓估计最深点多少米。", "limitation", "depth", None, None),
    ("B11", "手上没标签，直接给 USA_350244 的准确率打个百分数。", "limitation", "no_reference", None, None),
    ("B12", "把 Spain_5650136 水面的面积当作此次新增加的淹水面积写进报告。", "limitation", "new_flood", None, None),
    ("B13", "India_79637 的积水总量是多少立方米？没有水深资料。", "limitation", "depth", None, None),
    ("B14", "不要比较两种方法。用深度学习在 USA_527077 制图。", "report", "USA_527077", "map", "fcn"),
]


def check_task(answer, trace, expected, target, tool, method):
    actions = [row["call"] for row in trace if "call" in row]
    if answer["finish"] != expected:
        return False
    if expected == "limitation":
        return not actions and answer.get("reason") == target
    wanted = {"tool": tool, "chip": target}
    if method:
        wanted["method"] = method
    item = answer["result"]
    if actions != [wanted] or item["chip"] != target:
        return False
    if expected == "unavailable":
        return item["reason"] == ("no_observations" if target == "Paraguay_34417" else "unknown_chip")
    if item["kind"] != tool:
        return False
    if tool == "inspect":
        return 0 < item["valid_pixels"] <= item["total_pixels"]
    from csv import DictReader
    from math import isclose
    with (OUT / "test" / "per_chip.csv").open(encoding="utf-8") as stream:
        expected_areas = {row["method"]: float(row["observed_water_km2"])
                          for row in DictReader(stream) if row["chip"] == target}
    for selected in METHODS if tool == "compare" else (method,):
        evidence = item.get(selected)
        if not evidence or not isclose(evidence["area_km2"], expected_areas[selected], abs_tol=1e-10):
            return False
        with rasterio.open(ROOT / evidence["path"]) as observed:
            with rasterio.open(OUT / "test" / "masks" / selected / f"{target}.tif") as reference:
                if not matching_grid(observed, reference.profile) or not np.array_equal(
                        observed.read(1), reference.read(1)):
                    return False
    if tool == "compare":
        image, valid, profile = read_image(DATA / next(i for k, i, _ in read_split("test") if k == target))
        del image
        with rasterio.open(ROOT / item["otsu"]["path"]) as a, rasterio.open(ROOT / item["fcn"]["path"]) as b:
            expected_delta = (a.read(1) == 1) ^ (b.read(1) == 1)
        with rasterio.open(ROOT / item["disagreement_mask"]) as ds:
            if not matching_grid(ds, profile) or not np.array_equal(
                    ds.read(1), np.where(valid, expected_delta, NODATA)):
                return False
        if not isclose(item["disagreement_km2"], float(area_grid(profile)[expected_delta].sum())):
            return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?")
    parser.add_argument("--benchmark", action="store_true")
    parser.add_argument("--legacy", action="store_true", help="Use the original iterative tool loop")
    parser.add_argument("--model-dir", type=Path, default=DATA / "agent-model")
    args = parser.parse_args()
    if bool(args.question) == args.benchmark:
        parser.error("Provide a question or --benchmark")
    system = LEGACY_SYSTEM if args.legacy else SYSTEM
    llm = load_llm(args.model_dir, system)
    decide_fn = decide if args.legacy else decide_action
    tools = RasterTools()
    if args.question:
        record = decide_fn(llm, tools, args.question)
        dump(OUT / "agent" / "last_run.json", record)
        print(record["answer"].get("text", "无法完成任务；详见工具调用记录。"))
        return
    records = []
    for key, question, expected, target, tool, method in (TRANSFER if args.legacy else BENCHMARK + TRANSFER):
        record = decide_fn(llm, tools, question)
        record.update(id=key, expected=expected, target=target, expected_tool=tool, method=method,
                      passed=check_task(record["answer"], record["trace"], expected, target, tool, method))
        records.append(record)
        print(f"{key} {record['passed']} {record['answer']['finish']} {record['seconds']:.2f}s", flush=True)
        import mlx_lm
        dump(OUT / "agent" / ("legacy-transfer.json" if args.legacy else "benchmark.json"),
             {"model": MODEL, "revision": REVISION, "mlx_lm": mlx_lm.__version__,
              "prompt": system, "passed": sum(r["passed"] for r in records),
              "size": len(records), "records": records})


if __name__ == "__main__":
    main()
