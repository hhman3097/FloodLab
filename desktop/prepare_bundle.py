"""Build offline, pinned runtime bundles from original conda-forge packages."""
import argparse
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STAGE = HERE / "stage"
MAMBA = ROOT / ".tools/bin/micromamba"


def download(url, path, expected=None, algorithm="sha256"):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not expected or hashlib.new(algorithm, path.read_bytes()).hexdigest() == expected):
        return
    temporary = path.with_suffix(path.suffix + ".part")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as src, temporary.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            if expected and hashlib.new(algorithm, temporary.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Hash mismatch: {path.name}")
            temporary.replace(path)
            return
        except Exception:
            if attempt == 2:
                raise


def payload():
    target = STAGE / "payload"
    target.mkdir(parents=True, exist_ok=True)
    selections = [
        "event/gis_agent.py", "event/gis_client.py", "event/gis_tools.py", "event/dialogue.py", "event/flood_search.py", "event/skills/flood-discovery/SKILL.md",
        "event/runtime.py", "event/worker_bootstrap.py", "event/pi_bridge.py", "event/pi-worker.cjs", "event/responses_api.py",
        "event/inputs/assets", "event/inputs/context-s2-display.tif",
        "event/products/ems-reference.geojson", "event/cartography_data",
        "event/runs/gis-agent-pilot-v2", "event/runs/workbench_d0e0a1f802",
        "workbench/server.py", "workbench/projects.py", "workbench/public",
        "event/ems_project.py", "event/footprint_worker.py", "event/skills/event-workspace/SKILL.md",
        "workbench/node_modules/leaflet/dist", "workbench/node_modules/lucide/dist/umd",
        "workbench/node_modules/leaflet/LICENSE", "workbench/node_modules/lucide/LICENSE",
        "desktop/THIRD_PARTY.md", "desktop/QGIS-COPYING",
    ]
    for name in selections:
        source, dest = ROOT / name, target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, dest)
    # Keep the large research archive separate; the app carries its live case only.
    manifest = {"version": json.loads((HERE / "package.json").read_text())["version"], "original_root": str(ROOT),
                "files": [{"path": p.relative_to(target).as_posix(),
                           "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                          for p in sorted(target.rglob("*")) if p.is_file() and p.name != "payload.json"]}
    (target / "payload.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Payload: {len(manifest['files'])} files", flush=True)


def runtime(platform):
    folder = {"osx-arm64": "mac-arm64", "win-64": "win-x64"}[platform]
    target = STAGE / folder
    target.mkdir(parents=True, exist_ok=True)
    download("https://raw.githubusercontent.com/mamba-org/mamba/2.10.0/LICENSE", target / "MICROMAMBA-LICENSE")
    if platform == "osx-arm64":
        records = [json.loads(p.read_text()) for p in (ROOT / ".tools/qgis/conda-meta").glob("*.json")]
        shutil.copy2(MAMBA, target / "micromamba")
    else:
        lock = target / "solve.json"
        if not lock.exists():
            result = subprocess.run([
                str(MAMBA), "create", "--dry-run", "--json", "--no-rc",
                "-r", str(ROOT / ".tools/mamba"), "-p", str(target / "solve-only"),
                "--platform", platform, "-c", "conda-forge", "--override-channels",
                "qgis=3.44.14", "python=3.12", "shapely", "pyproj",
            ], capture_output=True, text=True, check=True)
            lock.write_text(result.stdout, encoding="utf-8")
        records = json.loads(lock.read_text())["actions"]["LINK"]
        archive = target / "micromamba.tar.bz2"
        download("https://micro.mamba.pm/api/micromamba/win-64/2.10.0", archive)
        with tarfile.open(archive) as source:
            content = source.extractfile("Library/bin/micromamba.exe")
            (target / "micromamba.exe").write_bytes(content.read())
        archive.unlink()
    def fetch(record):
        url = record.get("url") or record["channel"].rstrip("/") + "/" + record["fn"]
        filename = record.get("fn") or url.rsplit("/", 1)[-1]
        dest = target / "packages" / filename
        digest = record.get("sha256") or record.get("md5")
        algorithm = "sha256" if record.get("sha256") else "md5"
        download(url, dest, digest, algorithm)
        return {"file": filename, "url": url, "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                "md5": hashlib.md5(dest.read_bytes()).hexdigest(), "license": record.get("license", ""),
                "name": record["name"], "version": record["version"]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        packages = list(pool.map(fetch, records))
    executable = target / ("micromamba.exe" if platform == "win-64" else "micromamba")
    info = {"platform": platform, "qgis": "3.44.14", "packages": sorted(packages, key=lambda p: p["file"]),
            "installer_sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}
    info["id"] = hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest()[:16]
    (target / "runtime.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"{platform}: {len(packages)} pinned packages, {sum(p.stat().st_size for p in (target / 'packages').iterdir()) / 1e6:.0f} MB", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["osx-arm64", "win-64"])
    parser.add_argument("--payload", action="store_true")
    args = parser.parse_args()
    if args.payload:
        payload()
    if args.platform:
        runtime(args.platform)
