"""Package reviewable artifacts; exclude software environments and model caches."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "AI4Science-FloodLab-v3.zip"
TOP = [
    "README.md", "DELIVERY_CHECK.md", "TEAM_REVIEW.md", "EXPERIMENT_LOG.md",
    "pyproject.toml", "uv.lock", ".gitignore", "floodlab.py", "agent.py", "figures.py",
    "test_project.py", "rebase_paths.py", "make_delivery.py", "audit_delivery.py",
]


def files():
    selected = {ROOT / p for p in TOP}
    for directory in ("outputs", "event", "workbench", "researchwrite", "report", "delivery-qa"):
        for path in (ROOT / directory).rglob("*"):
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.is_symlink():
                continue
            if any(part in ("node_modules", "cache", "__pycache__", ".pytest_cache")
                   or part.startswith(".") for part in relative.parts):
                continue
            if path.suffix in (".pyc", ".tmp", ".qgz~", ".gpkg-shm", ".gpkg-wal"):
                continue
            if "main-before-workbench" in path.name:
                continue
            if path.name == "final-package-check.json":
                continue
            if relative.parts[:2] == ("report", "build") and path.name != "main.pdf":
                continue
            selected.add(path)
    return sorted(selected)


def main():
    manifest = []
    selected = files()
    for path in selected:
        data = path.read_bytes()
        if path.suffix in (".json", ".jsonl", ".py", ".md", ".js", ".html", ".tex", ".log"):
            if re.search(rb"\bsk-[A-Za-z0-9_-]{32,}", data):
                raise RuntimeError(f"Potential credential in {path.relative_to(ROOT)}")
        manifest.append({"path": str(path.relative_to(ROOT)), "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in selected:
            archive.write(path, "AI4Science/" + str(path.relative_to(ROOT)))
        archive.writestr("AI4Science/PACKAGE_MANIFEST.json",
                         json.dumps({"files": manifest, "excludes": [
                             "Python/QGIS environments", "Sen1Floods11 original data and weights",
                             "local MLX model", "node_modules", "temporary reproductions",
                             "old delivery archives"]}, indent=2))
    checksum = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    (ROOT / "AI4Science-FloodLab-v3.zip.sha256").write_text(f"{checksum}  {OUTPUT.name}\n")
    print(json.dumps({"archive": str(OUTPUT), "files": len(manifest),
                      "bytes": OUTPUT.stat().st_size, "sha256": checksum}, indent=2))


if __name__ == "__main__":
    main()
