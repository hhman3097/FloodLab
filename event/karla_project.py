"""Bind recovery-monitoring tools and immutable products to one Pi workspace."""
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path

from runtime import environment, executable
from sar_project import make_bundle, read, worker

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "inputs/assets/karla"
TOOL_NAMES = {"karla_inventory","karla_acquire","karla_segment","karla_compare","karla_exposure"}
METHODS = {"sar":"sar_change","optical":"optical_mndwi"}


def execute(project,name,args):
    records = project.index.setdefault("karla", {})

    def folder(key, kind):
        item = records.get(key)
        if not item or item["kind"] != kind:
            raise ValueError("Select a matching Karla result from this conversation")
        return project.root / item["path"]

    def create(prefix, name):
        key = prefix + "-" + uuid.uuid4().hex[:10]
        path = project.root / name / key
        path.mkdir(parents=True)
        return key,path

    def remember(key,path,kind):
        records[key]={"kind":kind,"path":path.relative_to(project.root).as_posix()}

    def publish(key,path,kind,raster,scope,label,extra=()):
        worker("karla_layout.py",path,raster,scope,timeout=95)
        stats=read(path/"results.json")
        files=[p for p in sorted(path.iterdir()) if p.is_file() and p.name!="project.zip"]
        make_bundle(path,[*files,*extra],project.root)
        result={**stats,"key":key,"kind":kind,"aoi":label,
                "observations":stats.get("dates",["2023-12-18","2023-12-19"]),
                "preview_file":"map.png","folder":path.relative_to(project.root).as_posix()}
        project.index["outputs"].append(result)
        return result

    if name=="karla_inventory":
        return {"case":"Karla Lake / EMSN184, Greece","grid":"20 m, UTM 34N","window":[22.55,39.43,22.85,39.65],
                "focus_options":{"study":"full study window","north":"north basin, [22.63,39.49,22.78,39.60]"},
                "observation_options":{"sar":{"dates":["2023-09-01","2023-12-18"],"orbit":7,"method":"sar_change"},
                                       "optical":{"dates":["2023-08-31","2023-12-19"],"method":"optical_mndwi"}},
                "active_workspace_ids":records,"rules":"Decide from the available observation date and interpretability. New optical evidence does not imply detected expansion or recession."}
    if name=="karla_acquire":
        sensor=args["sensor"]
        if sensor not in METHODS:
            raise ValueError("Use a listed sensor")
        existing=next((k for k,v in records.items() if v["kind"]==sensor),None)
        if existing:
            return {"download_id":existing,"reused":True,**read(folder(existing,sensor)/"download.json")}
        key,path=create(sensor,"inputs")
        try:
            worker("karla_data.py",path,sensor,timeout=280)
            mode="live_remote_window"
        except (RuntimeError,subprocess.TimeoutExpired):
            frozen=ASSETS/"observations"/sensor
            if not (frozen/"download.json").is_file():
                raise
            manifest=read(frozen/"download.json")
            for row in manifest["captures"]:
                p=frozen/row["file"]
                if hashlib.sha256(p.read_bytes()).hexdigest()!=row["sha256"]:
                    raise ValueError("Frozen observation hash mismatch")
                shutil.copy2(p,path/p.name)
            shutil.copy2(frozen/"download.json",path/"download.json")
            mode="verified_prefetch_fallback"
        remember(key,path,sensor)
        return {"download_id":key,"mode":mode,**read(path/"download.json")}
    if name=="karla_segment":
        method=args["algorithm"]
        if method not in METHODS.values():
            raise ValueError("Choose an implemented segmentation method")
        sensor=next(k for k,v in METHODS.items() if v==method)
        p=folder(args["download_id"],sensor)
        reason=str(args["reason"]).strip()
        if not 8 <= len(reason) <= 400:
            raise ValueError("Record a short evidence-based method rationale")
        key,path=create(sensor+"-mask","outputs")
        worker("karla_analysis.py","segment",p,path,method,timeout=95)
        stats=read(path/"results.json")
        stats["method_reason"]=reason
        (path/"results.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
        remember(key,path,sensor+"_mask")
        return publish(key,path,"karla_evidence",path/"evidence.tif","study",
                       "Karla 湖 · "+("雷达初报" if sensor=="sar" else "光学更新"),list(p.iterdir()))
    if name=="karla_compare":
        s=folder(args["sar_id"],"sar_mask")
        o=folder(args["optical_id"],"optical_mask")
        key,path=create("comparison","outputs")
        worker("karla_analysis.py","compare",s,o,path,timeout=90)
        if (ASSETS/"emsn184.gpkg").is_file():
            reference=read(ASSETS/"reference-source.json")
            if hashlib.sha256((ASSETS/"emsn184.gpkg").read_bytes()).hexdigest()!=reference["gpkg_sha256"]:
                raise ValueError("Official reference checksum mismatch")
            checks={}
            for label,product,layer in (("sar",s,"P04FLDEL01_FloodExtent"),("optical",o,"P04FLDEL02_FloodExtent")):
                raw=subprocess.run([str(executable("python")),str(HERE/"karla_analysis.py"),
                    "official",str(product/"evidence.tif"),str(ASSETS/"emsn184.gpkg"),layer],
                    env=environment(),capture_output=True,check=True,timeout=45)
                checks[label]=json.loads(raw.stdout)
            stats=read(path/"results.json")
            stats["official_reference"]=checks
            (path/"results.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
        remember(key,path,"comparison")
        return publish(key,path,"karla_evidence",path/"agreement.tif","study","Karla 湖 · 两期证据对照")
    if name=="karla_exposure":
        comparison=folder(args["comparison_id"],"comparison")
        scope=args["scope"]
        if scope not in ("study","north"):
            raise ValueError("Use the study or north window")
        key,path=create("exposure","outputs")
        land=ASSETS/"landcover"
        metadata=read(ASSETS/"roads-source.json")
        if hashlib.sha256((ASSETS/"roads.geojson").read_bytes()).hexdigest()!=metadata["roads_sha256"]:
            raise ValueError("Road archive checksum mismatch")
        worker("karla_analysis.py","exposure",comparison,land,ASSETS/"roads.geojson",path,scope,timeout=120)
        shutil.copy2(next(land.glob("*.tif")),path/"landcover.tif")
        shutil.copy2(ASSETS/"roads-source.json",path/"roads-source.json")
        shutil.copy2(land/"download.json",path/"landcover-source.json")
        stats=read(path/"results.json")
        stats["road_snapshot"]=metadata["snapshot"]
        (path/"results.json").write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding="utf-8")
        return publish(key,path,"karla_exposure",comparison/"agreement.tif",scope,
                       "Karla 湖 · "+("北部片区核查" if scope=="north" else "农田与道路核查"),[comparison/"agreement.tif"])
    raise ValueError("Unknown Karla tool")
