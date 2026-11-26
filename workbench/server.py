"""Local FloodLab workbench. Public source data and real QGIS/LLM execution."""
import argparse
import getpass
import hashlib
import hmac
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNS = ROOT / "event/runs"
sys.path.insert(0, str(ROOT / "event"))
from gis_agent import run
from responses_api import API
from projects import Projects
from ems_project import EventTools
from dialogue import decide
from runtime import executable, environment

MODEL = "gpt-6.1-sol"
BASE = "https://api.clawlab.chat/v1"
KEY = ""
JOBS = {}
LOCK = threading.Lock()
CACHE = HERE / "cache"
GEO_LOCK = threading.Lock()
DEMO = "gis-agent-pilot-v2"
PROJECT_PREVIEWS = {
    "karla_evidence": {"map.pdf","map.png","map.qgz","project.zip","results.json","evidence.tif","agreement.tif","candidates.gpkg"},
    "karla_exposure": {"map.pdf","map.png","map.qgz","project.zip","results.json","roads.csv","cropland.csv","roads.geojson"},
    "sar_flood_change": {"map.pdf","map.png","map.qgz","candidate.tif","candidate.gpkg","agreement.tif","results.json","project.zip"},
    "transport_screening": {"transport.pdf","transport.png","transport.qgz","segments.csv","municipalities.csv","corridors.csv","segments.geojson","analysis.json","project.zip"},
    "satellite_coverage": {"coverage.geojson","results.json"},
}
FOOTPRINT_FILES = {"map.pdf","map.png","map.qgz","grid.geojson","results.json"}


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def run_path(run_id):
    if Path(run_id).name!=run_id or run_id in (".",".."):raise ValueError("Unknown run")
    path = Projects(RUNS).directory(run_id).resolve()
    if not any((path / f).is_file() for f in ("agent-run.json","dialogue.json")):
        raise ValueError("Unknown run")
    return path


def registry(directory):
    return load(directory / "worker-checkpoint.json") or load(directory / "state.json", {})


def project_file(run_id,path):
    file=Path(path).resolve()
    root=Projects(RUNS).info(run_id).get("root")
    if ROOT not in file.parents and (not root or Path(root).resolve() not in file.parents):
        raise ValueError("文件不属于此工作区")
    return file


def geojson(path,project_root=None):
    path = Path(path)
    if not path.is_file() or (ROOT not in path.resolve().parents and project_root not in path.resolve().parents):
        raise ValueError("Unknown layer file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = CACHE / f"{digest}.geojson"
    with GEO_LOCK:
        if not target.exists():
            CACHE.mkdir(exist_ok=True)
            subprocess.run([str(executable("ogr2ogr")), "-f", "GeoJSON",
                            "-t_srs", "EPSG:4326", str(target), str(path)],
                           check=True, capture_output=True, timeout=60, env=environment())
    return target


def prepare_image():
    CACHE.mkdir(exist_ok=True)
    image = CACHE / "sentinel2.png"
    warped = CACHE / "sentinel2-web.tif"
    meta = CACHE / "image-bounds.json"
    if image.exists() and meta.exists():
        return
    subprocess.run([str(executable("gdalwarp")), "-overwrite", "-t_srs", "EPSG:4326",
                    "-ts", "1400", "0", "-r", "bilinear", str(ROOT / "event/inputs/context-s2-display.tif"),
                    str(warped)], check=True, capture_output=True, env=environment())
    subprocess.run([str(executable("gdal_translate")), "-of", "PNG", str(warped), str(image)],
                   check=True, capture_output=True, env=environment())
    info = json.loads(subprocess.check_output([str(executable("gdalinfo")), "-json", str(warped)],
                                             env=environment()))
    corners = info["cornerCoordinates"]
    meta.write_text(json.dumps({"bounds": [
        list(reversed(corners["lowerLeft"])), list(reversed(corners["upperRight"])),
    ], "date": "2024-10-26", "role": "Pre-event Sentinel-2 RGB context"}))


def summary(run_id):
    directory = run_path(run_id)
    data = load(directory / "agent-run.json", {})
    state = registry(directory)
    artifacts = []
    verified = []
    for name, item in state.get("artifacts", {}).items():
        artifacts.append({"id": name, "stale": item.get("stale", False),
                          "spec": item["spec"], "layer_id": item["inputs"][0],
                          "files": [kind for kind in ("pdf", "png", "path") if Path(item[kind]).exists()]})
        layer_file = Path(state["layers"][item["inputs"][0]]["path"])
        try:
            digest = hashlib.sha256(layer_file.read_bytes()).hexdigest()
        except OSError:
            artifacts[-1]["layer_available"]=False
            verified.append(None)
            continue
        artifacts[-1]["layer_available"]=True
        candidates = set(directory.glob("verification-*.json")) | set(layer_file.parent.glob("verification-*.json"))
        matches = [value for p in candidates if (value := load(p, {})).get("layer_sha256") == digest]
        verified.append(matches[0] if matches else None)
    turns = []
    for index, turn in enumerate(data.get("turns", [])):
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        for response in turn.get("responses", []):
            for key in usage:
                usage[key] += (response.get("usage") or {}).get(key, 0)
        turns.append({
            "question": turn["question"], "answer": turn.get("answer"),
            "status": turn["status"], "calls": turn.get("calls", []),
            "elapsed_s": turn.get("elapsed_s"), "timing_note": turn.get("timing_note"),
            "usage": usage, "error": turn.get("error"),
        })
    guide=load(directory/"dialogue.json",{})
    for entry in reversed(guide.get("turns",[])):
        turns.insert(min(entry["before_gis"],len(turns)),entry)
    return {"id": run_id,"project":Projects(RUNS).info(run_id),"event_outputs":guide.get("event_outputs",[]), "model": guide.get("model") or data.get("requested_model"), "status": guide.get("status") or data.get("status"),
            "turns": turns, "scope":guide.get("scope","local"), "artifacts": artifacts if guide.get("scope","local")=="local" else [], "parameters": state.get("parameters", {}),
            "verification": verified}


def worker(job_id, api, directory, question, resume, event):
    guide=load(directory/"dialogue.json",{"turns":[]})
    def save():
        tmp=directory/"dialogue.tmp"
        tmp.write_text(json.dumps(guide,ensure_ascii=False),encoding="utf-8")
        tmp.replace(directory/"dialogue.json")
    try:
        data=load(directory/"agent-run.json",{})
        previous=summary(directory.name)["turns"]
        history=[{"question":t["question"],"answer":(t.get("answer") or "")[:3000],"options":t.get("options",[])} for t in previous[-8:]]
        local=guide.get("scope")=="local" if guide.get("scope") else bool(data.get("turns"))
        guide.update(model=api.model,status="running")
        entry={"before_gis":len(data.get("turns",[])),"question":question,"status":"running","calls":[],"options":[]}
        guide["event_outputs"]=[]
        guide["turns"].append(entry);save()
        info=Projects(RUNS).info(directory.name)
        project=EventTools(info["root"]) if info else None
        decision=decide(api,question,history,guide,local,event,project=project)
        guide.update(scope=decision["scope"],evidence=decision["evidence"])
        entry.update(answer=decision["answer"],options=decision["options"],status="finished",kind="dialogue")
        if decision["searched"]:
            entry["calls"]=[{"name":"search_floods","arguments":json.dumps({"query":decision["evidence"]["query"]},ensure_ascii=False),
                             "response":{"status":"ok","result":decision["evidence"]}}]
        if project:
            entry["calls"].extend(project.audit)
            created=[c["response"]["result"] for c in project.audit
                     if c["name"] in {"analyse_footprint","search_satellite","publish_sar_map","export_transport_map",
                                      "karla_segment","karla_compare","karla_exposure"}
                     and c["response"]["status"]=="ok"]
            guide["event_outputs"]=list({o["folder"]:o for o in (created or decision["maps"])}.values())
            entry["event_outputs"]=guide["event_outputs"]
        if decision["task"]:
            entry.update(answer="按当前研究区执行以下空间分析。",options=[])
            save()
            run(api,directory,[decision["task"]],resume=bool(data.get("turns")),cancel_event=event,summarize_at_limit=True,max_calls=26)
        with LOCK:
            status="finished"
            if event.is_set():
                status="cancelled";entry.update(status=status,answer="任务已停止。",options=[])
            elif decision["task"]:
                record=load(directory/"agent-run.json",{})
                tail=(record.get("turns") or [{}])[-1]
                if tail.get("status")!="model_finished_unverified":
                    status="incomplete"
                    entry.update(status=status,answer="本轮空间分析未完成。"+("已达到工具调用上限。" if tail.get("status")=="tool_budget_exhausted" else "没有完整的执行结果。"),options=[])
            guide["status"]="execution_finished_not_independently_evaluated" if status=="finished" else status
            save();JOBS[job_id]["status"]=status
    except Exception as exc:
        reason=str(exc).replace(api.key,"[REDACTED]")
        with LOCK:
            status="cancelled" if event.is_set() else "failed"
            if "entry" in locals():entry.update(status=status,error=reason,answer="任务已停止。" if event.is_set() else "本轮未完成："+reason,options=[])
            guide["status"]=status;save()
            JOBS[job_id].update(status=status,error=reason)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Paths only; never request bodies, keys, or authorization headers.
        if args and isinstance(args[0], str) and "/api/" in args[0]:
            return
        super().log_message(fmt, *args)

    def allowed(self):
        token = os.environ.get("AI4S_DESKTOP_TOKEN")
        if token and not hmac.compare_digest(self.headers.get("X-FloodLab-Token", ""), token):
            return False
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
            return False
        origin = self.headers.get("Origin")
        return not origin or origin in (f"http://127.0.0.1:{self.server.server_port}",
                                         f"http://localhost:{self.server.server_port}")

    def send_json(self, body, status=200):
        content = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def send_file(self, path, download=False):
        path = Path(path)
        if not path.is_file():
            self.send_error(404)
            return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if not self.allowed():
            self.send_error(403)
            return
        query = urlparse(self.path)
        args = {k: v[0] for k, v in parse_qs(query.query).items()}
        try:
            if query.path == "/api/status":
                runs = sorted({p.parent.name for name in ("agent-run.json","dialogue.json") for p in RUNS.glob("*/"+name)
                               if p.parent.name == DEMO or p.parent.name.startswith("workbench_")},
                              reverse=True)
                latest={};titles={}
                for p in RUNS.glob("workbench_*/project.json"):
                    info=load(p,{})
                    if info.get("title"):titles[p.parent.name]=info["title"]
                    if info.get("id") and (p.stat().st_mtime_ns,p.parent.name)>latest.get(info["id"],(0,"")):
                        latest[info["id"]]=(p.stat().st_mtime_ns,p.parent.name)
                runs=[r for _,r in sorted(latest.values(),reverse=True)]+runs
                self.send_json({"model": MODEL, "base_url": BASE, "configured": bool(KEY),
                                "runs": runs, "titles":titles, "default_run": DEMO,
                                "jobs": [{k: v for k, v in j.items() if k != "event"} for j in JOBS.values()]})
            elif query.path == "/api/run":
                self.send_json(summary(args["id"]))
            elif query.path == "/api/layer":
                directory = run_path(args["run"])
                state = registry(directory)
                layer = state["layers"][args["id"]]
                file=project_file(args["run"],layer["path"])
                self.send_file(geojson(file,file.parent))
            elif query.path=="/api/project-output":
                directory=run_path(args["run"]);info=Projects(RUNS).info(args["run"])
                guide=load(directory/"dialogue.json",{})
                outputs=guide.get("event_outputs",[])+[o for t in guide.get("turns",[]) for o in t.get("event_outputs",[])]
                item=next((o for o in outputs if o["folder"]==args["folder"]),None)
                if not info or not item or args["file"] not in PROJECT_PREVIEWS.get(item.get("kind"),FOOTPRINT_FILES):raise ValueError("Unknown output")
                base=Path(info["root"]).resolve();p=(base/item["folder"]/args["file"]).resolve()
                if base not in p.parents:raise ValueError("Unknown output")
                self.send_file(p,download=args.get("download")=="1")
            elif query.path == "/api/context":
                sources = {
                    "aoi": ROOT / "event/inputs/assets/study-area.geojson",
                    "flood": ROOT / "event/products/ems-reference.geojson",
                    "roads": ROOT / "event/inputs/assets/roads-20241028.geojson",
                }
                self.send_file(geojson(sources[args["id"]]))
            elif query.path == "/api/image":
                self.send_json(load(CACHE / "image-bounds.json"))
            elif query.path == "/api/file":
                directory = run_path(args["run"])
                state = registry(directory)
                item = state["artifacts"][args["artifact"]]
                kind = args["kind"]
                if kind not in ("pdf", "png", "path"):
                    raise ValueError("Unsupported artifact")
                self.send_file(project_file(args["run"],item[kind]), download=args.get("download") == "1")
            elif query.path == "/api/data":
                state = registry(run_path(args["run"]))
                self.send_file(project_file(args["run"],state["layers"][args["id"]]["path"]), download=True)
            elif query.path == "/api/desktop-project" and os.environ.get("AI4S_DESKTOP_TOKEN"):
                state = registry(run_path(args["run"]))
                project = project_file(args["run"],state["artifacts"][args["artifact"]]["path"])
                if project.suffix != ".qgz" or not project.is_file():
                    raise ValueError("Unknown QGIS project")
                self.send_json({"path": str(project)})
            elif query.path == "/satellite.png":
                self.send_file(CACHE / "sentinel2.png")
            elif query.path.startswith("/vendor/leaflet/"):
                self.static(HERE / "node_modules/leaflet/dist", query.path[len("/vendor/leaflet/"):])
            elif query.path == "/vendor/lucide.js":
                self.send_file(HERE / "node_modules/lucide/dist/umd/lucide.min.js")
            else:
                self.static(HERE / "public", query.path.lstrip("/") or "index.html")
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            self.send_json({"error": str(exc)}, 400)

    def static(self, root, suffix):
        path = (root / unquote(suffix)).resolve()
        if root.resolve() not in path.parents:
            self.send_error(403)
            return
        self.send_file(path)

    def do_POST(self):
        global KEY, BASE, MODEL
        if not self.allowed():
            self.send_error(403)
            return
        candidate = ""
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 16000 or "application/json" not in self.headers.get("Content-Type", ""):
                raise ValueError("Invalid JSON request")
            body = json.loads(self.rfile.read(size))
            if self.path=="/api/folder":
                self.send_json(Projects(RUNS).bookmark(body["parent"]))
            elif self.path == "/api/connection":
                candidate = body.get("key", "").strip()
                if not candidate:
                    raise ValueError("请输入 API key")
                base = body.get("base_url", BASE).strip().rstrip("/")
                model = body.get("model", MODEL).strip()
                parsed = urlparse(base)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.query or parsed.fragment:
                    raise ValueError("请输入有效的 API 服务地址")
                if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
                    raise ValueError("远程服务地址须使用 HTTPS")
                if not model or len(model) > 200:
                    raise ValueError("请输入模型名称")
                api = API(base, candidate, model)
                response = api.request("/responses", {
                    "model": model, "input": "Reply OK.", "store":False, "max_output_tokens":256,
                })
                if not any(p.get("type")=="output_text" and p.get("text") for item in response.get("output",[]) for p in item.get("content",[])):
                    raise ValueError("服务未返回有效的模型响应")
                with LOCK:
                    KEY, BASE, MODEL = candidate, base, model
                self.send_json({"configured": True, "model": MODEL, "base_url": BASE})
            elif self.path == "/api/disconnect":
                KEY = ""
                self.send_json({"configured": False})
            elif self.path == "/api/stop":
                with LOCK:
                    job=JOBS[body["id"]]
                    if job["status"]=="running":
                        job["event"].set();job["status"]="stopping"
                    status=job["status"]
                self.send_json({"status":status})
            elif self.path == "/api/task":
                question = body.get("question", "").strip()
                if not question or len(question) > 5000:
                    raise ValueError("请输入不超过 5000 字的分析要求")
                if not KEY:
                    self.send_json({"error": "请先连接模型服务"}, 409)
                    return
                with LOCK:
                    if any(j["status"] in ("running", "stopping") for j in JOBS.values()):
                        self.send_json({"error": "已有分析正在执行"}, 409)
                        return
                    identifier = "workbench_" + uuid.uuid4().hex[:10]
                    source = body.get("run_id")
                    origin=run_path(source) if source else None
                    data=load(origin/"agent-run.json",{}) if origin else {}
                    guide=load(origin/"dialogue.json",{}) if origin else {}
                    guide.setdefault("turns",[])
                    if data and (data.get("status")!="execution_finished_not_independently_evaluated" or guide.get("status") in {"incomplete","cancelled","failed"}):
                        # Keep the original checkpoint intact. A follow-up starts a fresh GIS loop.
                        guide["turns"]=[{**t,"before_gis":0} for t in summary(source)["turns"]]
                        guide["event_outputs"]=[]
                        data={}
                    directory,info=Projects(RUNS).bind(identifier,source,body.get("folder_key"),question)
                    if data:
                        for name in ("agent-run.json", "state.json", "worker-checkpoint.json", "tools.jsonl"):
                            if (origin / name).exists():
                                shutil.copy2(origin / name, directory / name)
                    if source and data:
                        data.setdefault("forks", []).append({
                            "source_run": source, "previous_model": data["requested_model"],
                            "previous_provider": data["provider"], "model": MODEL, "provider": BASE,
                        })
                        data["requested_model"], data["provider"] = MODEL, BASE
                        (directory / "agent-run.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
                    if type(body.get("utc_offset_minutes")) is int:
                        guide["utc_offset_minutes"]=body["utc_offset_minutes"]
                    (directory/"dialogue.json").write_text(json.dumps(guide,ensure_ascii=False),encoding="utf-8")
                    event = threading.Event()
                    JOBS[identifier] = {"id": identifier, "run_id": identifier, "status": "running",
                                        "event": event}
                    threading.Thread(target=worker, args=(
                        identifier, API(BASE, KEY, MODEL), directory, question, bool(source), event),
                        daemon=True).start()
                self.send_json({"job_id": identifier, "run_id": identifier})
            else:
                self.send_error(404)
        except Exception as exc:
            text = str(exc)
            if KEY:
                text = text.replace(KEY, "[REDACTED]")
            if candidate:
                text = text.replace(candidate, "[REDACTED]")
            self.send_json({"error": text}, 400)


def main():
    global KEY, BASE, MODEL, RUNS
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ask-key", action="store_true")
    parser.add_argument("--desktop-stdin", action="store_true")
    parser.add_argument("--runs-dir", type=Path)
    args = parser.parse_args()
    if args.runs_dir:
        RUNS = args.runs_dir.resolve()
        RUNS.mkdir(parents=True, exist_ok=True)
    KEY = os.environ.get("AI4S_API_KEY", "")
    if args.desktop_stdin:
        config = json.loads(sys.stdin.readline())
        KEY = config.get("key", "")
        BASE = config.get("base_url", BASE)
        MODEL = config.get("model", MODEL)
    if args.ask_key and not KEY:
        KEY = getpass.getpass("API key (memory only): ")
    prepare_image()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"FloodLab: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        for job in JOBS.values():
            job["event"].set()
        server.server_close()


if __name__ == "__main__":
    main()
