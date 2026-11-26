"""Workspace-scoped Sentinel-1 acquisition, segmentation and map publication."""
import json
import subprocess
import uuid
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from runtime import environment, executable

HERE = Path(__file__).resolve().parent
CASE = "valencia-horta-sud-2024"
PRE_DATES = ("2024-10-08", "2024-10-20")
POST_DATE = "2024-11-01"


def cases():
    return {"case": CASE, "aoi": "Horta Sud, Valencia", "flood_event_date": "2024-10-29",
            "collection": "Sentinel-1 RTC", "polarizations": ["VV", "VH"],
            "relative_orbit": 110, "bbox_lon_lat": [-.52, 39.37, -.36, 39.46],
            "before_options": list(PRE_DATES), "after_options": [POST_DATE],
            "resolution_m": 20, "presets": ["balanced", "conservative"],
            "limit": "Only this independently prepared AOI is supported by the pixel pipeline."}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def worker(script, *args, timeout):
    result = subprocess.run([str(executable("python")), str(HERE / script), *map(str, args)],
                            env=environment(), capture_output=True, timeout=timeout)
    if result.returncode:
        error = result.stderr.decode(errors="replace").splitlines()
        tail = (error[-1] if error else "no diagnostic")[:300]
        if "?" in tail or "sig=" in tail:
            tail = tail.split("?", 1)[0]
        raise RuntimeError(f"{script}: {tail}")


def make_bundle(folder, files, base):
    path = folder / "project.zip"
    tmp = folder / ".bundle.tmp"
    try:
        with ZipFile(tmp, "w", ZIP_DEFLATED) as bundle:
            for file in files:
                bundle.write(file, file.relative_to(base))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def run_tool(project, name, args):
    acquired = project.index.setdefault("sar_downloads", {})
    masks = project.index.setdefault("sar_segments", {})
    if name == "sar_inventory":
        return {**cases(), "downloads": list(acquired), "segmentations": list(masks)}
    if name == "download_sar_pair":
        if args["case"] != CASE or args["before"] not in PRE_DATES or args["after"] != POST_DATE:
            raise ValueError("Use the Valencia case and listed pre/post dates")
        key = "sar-" + uuid.uuid4().hex[:10]
        folder = project.root / "inputs" / key
        folder.mkdir(parents=True)
        worker("sar_acquire.py", folder, args["before"], args["after"], timeout=250)
        result = read(folder / "download.json")
        acquired[key] = {"folder": str(folder.relative_to(project.root))}
        return {"download_id": key, **result}
    if name == "segment_sar_flood":
        download = acquired.get(args["download_id"])
        if not download or args["preset"] not in ("balanced", "conservative"):
            raise ValueError("Use a verified download ID from this conversation and an available preset")
        key = "change-" + uuid.uuid4().hex[:10]
        target = project.root / "outputs" / key
        target.mkdir(parents=True)
        worker("sar_analysis.py", "segment", project.root / download["folder"], target, args["preset"], timeout=90)
        masks[key] = {"folder": str(target.relative_to(project.root)), "download_id": args["download_id"]}
        return {"analysis_id": key, **read(target / "segmentation.json")}
    if name != "publish_sar_map":
        raise ValueError("Unknown SAR tool")
    analysis = masks.get(args["analysis_id"])
    if not analysis:
        raise ValueError("Use a segmentation ID from this conversation")
    folder = project.root / analysis["folder"]
    was_published = next((p for p in project.index["outputs"] if p["folder"] == analysis["folder"]), None)
    if was_published:
        return was_published
    worker("sar_analysis.py", "summarize", folder, timeout=90)
    worker("sar_layout.py", folder, timeout=90)
    summary = read(folder / "results.json")
    original = project.root / acquired[analysis["download_id"]]["folder"]
    names = ("candidate.tif", "candidate.gpkg", "agreement.tif", "segmentation.json", "results.json",
             "map.pdf", "map.png", "map.qgz", "north.svg")
    make_bundle(folder, [*(folder / n for n in names), *sorted(original.glob("*.tif")), original / "download.json"], project.root)
    result = {**summary, "towns": summary["towns"][:8], "kind":"sar_flood_change", "key": args["analysis_id"],
              "aoi":"瓦伦西亚 Horta Sud", "folder":analysis["folder"], "total_km2":summary["candidate_km2"],
              "observations":summary["dates"], "preview_file":"map.png"}
    project.index["outputs"].append(result)
    return result
