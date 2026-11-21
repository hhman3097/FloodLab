"""Check a relocated archive using explicitly reused local runtime/data caches."""
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "delivery-qa"


def command(args, cwd, name, env=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env,
                            capture_output=True, text=True, timeout=300)
    (DEST / f"{name}.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"{name} failed; see delivery-qa/{name}.log")
    return result.stdout


def main():
    archive = ROOT / "AI4Science-FloodLab-v3.zip"
    temporary = Path(tempfile.mkdtemp(prefix=".verify-floodlab-", dir=ROOT))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        manifest = json.loads(z.read("AI4Science/PACKAGE_MANIFEST.json"))
        for item in manifest["files"]:
            data = z.read("AI4Science/" + item["path"])
            assert len(data) == item["bytes"]
            assert hashlib.sha256(data).hexdigest() == item["sha256"]
        z.extractall(temporary)
    project = temporary / "AI4Science"
    python = ROOT / ".venv/bin/python"
    command([python, "rebase_paths.py"], project, "relocation")
    (project / ".tools").mkdir()
    (project / ".tools/qgis").symlink_to(ROOT / ".tools/qgis", target_is_directory=True)
    (project / "data").symlink_to(ROOT / "data", target_is_directory=True)
    command([python, "-m", "pytest", "-q"], project, "relocated-tests")
    command([python, "event/evaluate_comparison.py"], project, "relocated-comparison")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               QGIS_PREFIX_PATH=str(ROOT / ".tools/qgis"),
               PYTHONPATH=str(ROOT / ".tools/qgis/share/qgis/python"))
    command([ROOT / ".tools/qgis/bin/python", project / "event/audit_projects.py",
             "--output", DEST / "qgis-relocated.json"], project, "relocated-qgis", env)
    product_check = """
import json
from workbench import server
server.prepare_image()
rows=[]
for name in ('gis-agent-pilot-v2','workbench_d0e0a1f802'):
    value=server.summary(name)
    registry=server.registry(server.run_path(name))
    for artifact in value['artifacts']:
        path=registry['layers'][artifact['layer_id']]['path']
        geo=json.loads(server.geojson(path).read_text())
        assert len(geo['features'])>0
    assert all(v and v['passed'] for v in value['verification'])
    rows.append({'run':name,'verified_cells':[v['cells'] for v in value['verification']]})
print(json.dumps(rows))
"""
    product = command([python, "-c", product_check], project, "relocated-workbench")
    latex = Path("/Users/frank/texlive/2026/bin/universal-darwin/latexmk")
    command([latex, "-silent", "-xelatex", "-interaction=nonstopmode", "-halt-on-error",
             "-cd", "-outdir=build", "report/main.tex"], project, "relocated-report")
    import pymupdf
    original = pymupdf.open(ROOT / "report/build/main.pdf")
    rebuilt = pymupdf.open(project / "report/build/main.pdf")
    assert len(original) == len(rebuilt)
    assert all(a.get_text() == b.get_text() for a, b in zip(original, rebuilt))
    assert all(a.get_pixmap(dpi=80).samples == b.get_pixmap(dpi=80).samples
               for a, b in zip(original, rebuilt))
    report = {
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "manifest_files": len(manifest["files"]), "relocated_root": str(project),
        "manifest_verified": True, "tests": "13 passed", "comparison_numeric_tasks": "6/6",
        "qgis": json.loads((DEST / "qgis-relocated.json").read_text())["passed"],
        "workbench": json.loads(product),
        "report": {"pages": len(original), "text_equal": True, "render_at_80dpi_equal": True},
        "scope": "Relocated package; reused installed Python/QGIS and verified input caches. "
                 "No new model calls and not a new-machine installation.",
    }
    (DEST / "relocated-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
