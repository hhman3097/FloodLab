"""Real model/tool loop over the QGIS worker; no natural-language routing rules."""
import argparse
import base64
import getpass
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gis_client import GISClient

SYSTEM = """You are a GIS analyst executing the user's spatial analysis requests.
Use the actual registered layers and atomic QGIS tools. Inspect results and correct
errors. Do not invent computations, IDs, fields, or output paths. Data: historical
OSM road snapshot 2024-10-28, EMSR773 flood delineation from 2024-10-31, Horta Sud AOI.
Road exposure is planar overlap, not proven damage, closure or depth; bridges and
tunnels lack elevation assessment. Source coverage is not complete ground truth.

Layer references in process params MUST be objects {"layer":"registered_id"}.
Parameter references may be {"parameter":"key"}; store user-controlled analysis
choices with set_parameters and reference them in operations so their use is
auditable. IDs are immutable. A changed request may require new operations and new
outputs. Never present an old result as satisfying changed requirements.
All output geometry/statistics must be obtained by tools. Use algorithm_help when
needed; native QGIS defaults apply. Project to EPSG:32630 for metre measurements.
Clip roads to AOI, and dissolve coincident geometries within the selected road set
before measuring. Use same road selection in numerator and denominator. Null for
zero denominator. Grid starts at AOI upper-left in projected CRS; complete square
cells cover AOI. Do not clip the grid cells, clip input roads to AOI.
Show actual cell IDs and values in rankings. Render the requested map in QGIS;
read its screenshot with view_map and revise if needed, at most two image reviews.
Map titles/source notes must be concise English (font/layout portability); final
answer should be Chinese. If plotting a fraction use units of fraction (0-1), or
calculate a percent field first. Export_map class boundaries must cover all values.
You have at most 20 tool calls per user turn. Summarize only verified results and
explicitly describe anything incomplete. Do not claim success from an intended plan.
"""


def spec(name, description, properties, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required if required is not None else list(properties),
                       "additionalProperties": False},
    }}


STRING = {"type": "string"}
TOOLS = [
    spec("state", "Get registered layers and current parameters.", {}),
    spec("inspect", "Read CRS, fields, extent and sample values.", {"layer_id": STRING}),
    spec("set_parameters", "Record changed user-controlled analysis parameters.",
         {"values": {"type": "object"}}),
    spec("algorithm_help", "Read installed QGIS algorithm parameters and enum options.",
         {"algorithm": STRING}),
    spec("process", "Execute ONE native QGIS operation; output layer is registered. "
         "Allowed: native:reprojectlayer,fixgeometries,clip,intersection,dissolve,"
         "creategrid,extractbyexpression,fieldcalculator,sumlinelengths,aggregate,"
         "countpointsinpolygon. For input layers use {\"layer\":\"id\"}. "
         "Do not set OUTPUT; use a new output_id.",
         {"algorithm": STRING, "params": {"type": "object"}, "output_id": STRING}),
    spec("table", "Inspect numeric attributes, ranking and numeric sums.",
         {"layer_id": STRING, "fields": {"type": "array", "items": STRING},
          "sort_by": STRING, "descending": {"type": "boolean"}, "limit": {"type": "integer"}},
         ["layer_id", "fields"]),
    spec("export_map", "Export an editable QGIS project and A3 PDF/PNG thematic map. "
         "Automatic layout contains map frame, coordinate ticks, legend, grid north,"
         "scale bar, sources, projection and evidence limits. Class boundaries must "
         "cover the selected numeric field. This performs no spatial analysis.",
         {"layer_id": STRING, "field": STRING, "title": STRING, "unit": STRING,
          "output_id": STRING, "breaks": {"type": "array", "items": {"type": "number"}},
          "source_note": STRING, "decimals": {"type": "integer"}},
         ["layer_id", "field", "title", "unit", "output_id", "breaks", "source_note"]),
    spec("view_map", "Read an exported map image for visual inspection.",
         {"artifact_id": STRING}),
]


class API:
    def __init__(self, base, key, model):
        self.base, self.key, self.model = base.rstrip("/"), key, model

    def request(self, route, body=None):
        request = Request(self.base + route,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={"Authorization": f"Bearer {self.key}",
                                   "Content-Type": "application/json",
                                   "User-Agent": "AI4Science-GIS-Agent/0.3"})
        try:
            with urlopen(request, timeout=180) as response:
                return json.load(response)
        except HTTPError as exc:
            # Never include headers or request credentials in recorded errors.
            detail = exc.read().decode(errors="replace")[:1200].replace(self.key, "[REDACTED]")
            raise RuntimeError(f"Provider HTTP {exc.code}: {detail}") from None

    def complete(self, messages):
        return self.request("/chat/completions", {
            "model": self.model, "messages": messages, "tools": TOOLS,
            "tool_choice": "auto", "parallel_tool_calls": False,
            "max_tokens": 3000,
        })


def replay_messages(messages, turns, images):
    """Restore conversation from recorded results without re-executing any tools."""
    for turn in turns:
        messages.append({"role": "user", "content": turn["question"]})
        executed = iter(turn["calls"])
        for response in turn["responses"]:
            message = response["message"]
            messages.append({k: message[k] for k in ("role", "content", "tool_calls")
                             if k in message and message[k] is not None})
            for call in message.get("tool_calls", []):
                saved = next(executed)
                if (call["function"]["name"] != saved["name"]
                        or call["function"]["arguments"] != saved["arguments"]):
                    raise ValueError("Incomplete response checkpoint; inspect tool journal")
                result = saved["response"]
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})
                if saved["name"] == "view_map" and result["status"] == "ok" and images:
                    image = Path(result["result"]["png"]).read_bytes()
                    uri = "data:" + "image/png" + ";base64," + base64.b64encode(image).decode()
                    messages.append({"role": "user", "content": [
                        {"type": "text", "text": "Actual rendered map for your requested visual review."},
                        {"type": "image_url", "image_url": {"url": uri}},
                    ]})


def run(api, workspace, questions, tracked=True, images=True, resume=False, cancel_event=None):
    workspace = Path(workspace)
    record = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "provider": api.base, "requested_model": api.model,
        "condition": "tracked" if tracked else "generic",
        "images_enabled": images, "max_tool_calls_per_turn": 20,
        "questions": questions, "turns": [],
        "status": "running",
        "system_prompt": SYSTEM,
        "system_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / "agent-run.json"
    if resume:
        record = json.loads(path.read_text())
        if record["requested_model"] != api.model or record["provider"] != api.base:
            raise ValueError("Resume must use the same model and provider")
        if record["condition"] != ("tracked" if tracked else "generic") or record["images_enabled"] != images:
            raise ValueError("Resume must use the same experimental condition")
        if questions:
            record["questions"].extend(questions)
        questions = record["questions"]
        record.setdefault("resumptions_utc", []).append(datetime.now(timezone.utc).isoformat())
    elif path.exists():
        raise ValueError("Run exists; use --resume or a new workspace")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    with GISClient(workspace, tracked=tracked, resume=resume) as gis:
        system = SYSTEM if images else SYSTEM + "\nImage input is disabled in this experimental condition; do not request view_map. Other tools remain available."
        record["effective_system_prompt"] = system
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": "Available data and algorithms:\n" +
                     json.dumps(gis.ready, ensure_ascii=False)}]
        if resume:
            replay_messages(messages, record["turns"], images)
        for index, question in enumerate(questions):
            if index < len(record["turns"]):
                turn = record["turns"][index]
                if turn["status"] in ("model_finished_unverified", "tool_budget_exhausted"):
                    continue
                if index != len(record["turns"]) - 1:
                    raise ValueError("Only the last incomplete turn can resume")
                turn.setdefault("interruptions", []).append({"after_calls": len(turn["calls"])})
                turn["status"] = "running"
            else:
                messages.append({"role": "user", "content": question})
                turn = {"question": question, "calls": [], "responses": [], "status": "running"}
                record["turns"].append(turn)
            image_reviews = sum(c["name"] == "view_map" and c["response"]["status"] == "ok"
                                for c in turn["calls"])
            start = time.monotonic()
            for _ in range(21):
                if cancel_event is not None and cancel_event.is_set():
                    turn["status"] = "cancelled"
                    break
                if len(turn["calls"]) >= 20:
                    turn["status"] = "tool_budget_exhausted"
                    break
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
                try:
                    result = api.complete(messages)
                except Exception as exc:
                    turn["status"] = "provider_error"
                    turn["error"] = str(exc)
                    turn["elapsed_s"] = time.monotonic() - start
                    record["status"] = "provider_error"
                    path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
                    raise
                message = result["choices"][0]["message"]
                turn["responses"].append({
                    "model": result.get("model"), "usage": result.get("usage"),
                    "finish_reason": result["choices"][0].get("finish_reason"),
                    "message": message,
                })
                # Keep only supported message keys when forwarding provider responses.
                forwarded = {key: message[key] for key in ("role", "content", "tool_calls")
                             if key in message and message[key] is not None}
                messages.append(forwarded)
                calls = message.get("tool_calls", [])
                if not calls:
                    turn["answer"] = message.get("content")
                    turn["status"] = "model_finished_unverified"
                    break
                for call in calls:
                    if len(turn["calls"]) >= 20:
                        response = {"status": "error", "message": "Tool budget exhausted"}
                    else:
                        name = call["function"]["name"]
                        try:
                            arguments = json.loads(call["function"]["arguments"])
                            if name == "view_map" and (not images or image_reviews >= 2):
                                response = {"status": "error", "message": "Image review unavailable or budget exhausted"}
                            else:
                                response = gis.call(name, **arguments)
                        except (ValueError, TypeError) as exc:
                            response = {"status": "error", "message": str(exc)}
                        turn["calls"].append({"name": name, "arguments": call["function"]["arguments"],
                                              "response": response})
                        print(f"turn={len(record['turns'])} call={len(turn['calls'])} "
                              f"{name}: {response['status']}", flush=True)
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": json.dumps(response, ensure_ascii=False)})
                    if (call["function"]["name"] == "view_map" and response["status"] == "ok"
                            and images and image_reviews < 2):
                        image_reviews += 1
                        image = Path(response["result"]["png"]).read_bytes()
                        messages.append({"role": "user", "content": [
                            {"type": "text", "text": "Actual rendered map for your requested visual review."},
                            {"type": "image_url", "image_url": {
                                "url": "data:image/png;base64," + base64.b64encode(image).decode()}}
                        ]})
                path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            turn["elapsed_s"] = turn.get("elapsed_s", 0) + time.monotonic() - start
            if turn.get("interruptions"):
                turn["timing_note"] = "Excludes time before interruption if it was not checkpointed."
            turn["image_reviews"] = image_reviews
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
            if turn["status"] == "cancelled":
                break
        record["status"] = ("cancelled" if cancel_event is not None and cancel_event.is_set()
                            else "execution_finished_not_independently_evaluated")
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
    print(json.dumps({"record": str(path), "status": record["status"]}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="https://api.clawlab.chat/v1")
    parser.add_argument("--model")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--question", action="append")
    parser.add_argument("--generic", action="store_true")
    parser.add_argument("--no-images", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("AI4S_API_KEY") or getpass.getpass("API key (not stored): ")
    api = API(args.base_url, key, args.model)
    if args.list_models:
        result = api.request("/models")
        print(json.dumps([m["id"] for m in result["data"] if "gpt" in m["id"].lower()], indent=2))
        return
    if not args.model or not args.workspace or (not args.question and not args.resume):
        parser.error("--model, --workspace and --question are required for a run")
    run(api, args.workspace, args.question, tracked=not args.generic, images=not args.no_images,
        resume=args.resume)


if __name__ == "__main__":
    main()
