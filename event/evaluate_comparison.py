"""Recompute numeric outputs; leave semantic and visual judgments for explicit review."""
import hashlib
import json
from pathlib import Path

from verify_gis import read_features, verify

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "runs/gis-comparison"


def main():
    summary = {"replicates": 1, "conditions": {},
               "scope": "Development probes; not held-out population success rates."}
    for name in ("tracked", "generic", "no_image"):
        directory = DEST / name
        record = json.loads((directory / "agent-run.json").read_text())
        assert record["status"] == "execution_finished_not_independently_evaluated"
        state = json.loads((directory / "worker-checkpoint.json").read_text())
        results = []
        for index, turn in enumerate(record["turns"]):
            row = {
                "turn": index + 1, "question": turn["question"], "answer": turn["answer"],
                "calls": len(turn["calls"]),
                "errors": sum(c["response"]["status"] != "ok" for c in turn["calls"]),
                "image_reviews": turn["image_reviews"],
                "elapsed_s": turn["elapsed_s"],
                "usage": {key: sum(r.get("usage", {}).get(key, 0) for r in turn["responses"])
                          for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
            }
            maps = [json.loads(c["arguments"]) for c in turn["calls"]
                    if c["name"] == "export_map" and c["response"]["status"] == "ok"]
            row["maps"] = maps
            if index < 2 and maps:
                spec = maps[-1]
                layer = Path(state["layers"][spec["layer_id"]]["path"])
                fields = read_features(layer)[0]["properties"]
                candidates = [f for f in fields if f in (
                    "exposure_pct", "ratio", "exposure_ratio", "exposure_rate")]
                assert len(candidates) == 1, f"Review ratio field manually: {fields}"
                ratio = candidates[0]
                result = verify(
                    layer, ratio_field=ratio, ratio_scale=100 if ratio == "exposure_pct" else 1,
                    grid_size=1000 if index == 0 else 2000,
                    classes=None if index == 0 else
                    ["primary", "primary_link", "secondary", "secondary_link"])
                output = directory / f"verification-turn{index + 1}.json"
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
                row["numeric_verification"] = result
                artifact = state["artifacts"][spec["output_id"]]
                row["map_files_exist"] = all(Path(artifact[k]).is_file()
                                              for k in ("path", "pdf", "png"))
                row["requested_metric_correct"] = (
                    (spec["field"] == "exposed_m" and spec["unit"] == "m") if index == 0 else
                    spec["field"] == ratio and spec["unit"] in (
                        "%", "percent", "fraction", "fraction (0-1)"))
            row["semantic_review"] = "See REVIEW.md; Codex review, not student/human scoring."
            results.append(row)
        summary["conditions"][name] = {
            "record_sha256": hashlib.sha256((directory / "agent-run.json").read_bytes()).hexdigest(),
            "turns": results,
        }
    (DEST / "evaluation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({name: [
        {"turn": t["turn"], "calls": t["calls"], "errors": t["errors"],
         "numeric": t.get("numeric_verification", {}).get("passed")}
        for t in data["turns"]] for name, data in summary["conditions"].items()}, indent=2))


if __name__ == "__main__":
    main()
