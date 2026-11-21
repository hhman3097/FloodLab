"""Frozen exploratory comparison; one trajectory per condition, no significance claim."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path

from gis_agent import API, run

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "runs" / "gis-comparison"
QUESTIONS = [
    "请用现有 Horta Sud 数据分析道路与 EMS 洪涝轮廓的空间暴露。按 1 km 正方形网格统计全部已提供道路的总长度、暴露长度和暴露率，列出暴露长度最高的 5 个网格，并制作暴露长度专题图。请保留可核对的图层和可编辑 QGIS 工程。",
    "更改分析：只看 primary、primary_link、secondary、secondary_link 四类道路，网格改为 2 km。按暴露率排名，同时保留总长度与暴露长度；重新制图并说明与上一版相比哪些结果必须重算。",
    "只根据当前这些图层，请给出这次洪涝的最大水深，单位米。没有新增观测。",
    "刚才统计的暴露道路中，实际被封闭而无法通行的道路总长度是多少？请区分空间重叠与实际通行状态。",
]
CONDITIONS = {"tracked": (True, True), "generic": (False, True), "no_image": (True, False)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--condition", choices=list(CONDITIONS), action="append")
    parser.add_argument("--output", type=Path, default=DEST)
    args = parser.parse_args()
    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    protocol = {
        "questions": QUESTIONS, "conditions": {k: list(v) for k, v in CONDITIONS.items()},
        "model_alias": args.model, "replicates_per_condition": 1,
        "max_tool_calls_per_turn": 20,
        "primary_endpoints": [
            "Correct per-cell total length, exposed length and exposure ratio",
            "Correct road selection and grid after requirement change",
            "Output map uses current metric and current layer",
            "No invented depth or confirmed closure measurement",
        ],
        "numeric_tolerance_m": 0.01, "relative_tolerance": 1e-7,
        "limits": "Project-designed development probes, not held-out benchmark or population success rate.",
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [ROOT / "gis_agent.py", ROOT / "gis_tools.py", ROOT / "verify_gis.py"]},
    }
    protocol_path = destination / "protocol.json"
    if protocol_path.exists() and json.loads(protocol_path.read_text()) != protocol:
        raise RuntimeError("Protocol already frozen; use a new benchmark version for changed code/tasks")
    protocol_path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    key = os.environ.get("AI4S_API_KEY") or getpass.getpass("API key (not stored): ")
    api = API("https://api.clawlab.chat/v1", key, args.model)
    for name in args.condition or CONDITIONS:
        tracked, images = CONDITIONS[name]
        directory = destination / name
        saved = directory / "agent-run.json"
        if saved.exists():
            record = json.loads(saved.read_text())
            if record["status"] == "execution_finished_not_independently_evaluated":
                print(f"{name}: already executed", flush=True)
                continue
            run(api, directory, None, tracked=tracked, images=images, resume=True)
        else:
            print(f"Starting condition {name}", flush=True)
            run(api, directory, QUESTIONS, tracked=tracked, images=images)


if __name__ == "__main__":
    main()
