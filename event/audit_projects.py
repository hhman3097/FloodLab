"""Read every packaged QGIS project and check data/graphic references."""
import argparse
import json
from pathlib import Path

from qgis.core import QgsApplication, QgsLayoutItemPicture, QgsProject

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    rows = []
    for path in sorted(ROOT.rglob("*.qgz")):
        project = QgsProject()
        opened = project.read(str(path))
        invalid = [{"name": layer.name(), "source": layer.source()}
                   for layer in project.mapLayers().values() if not layer.isValid()]
        pictures = []
        for layout in project.layoutManager().layouts():
            for item in layout.items():
                if isinstance(item, QgsLayoutItemPicture):
                    source = item.picturePath()
                    if source and not Path(source).is_file():
                        pictures.append(source)
        rows.append({"project": str(path.relative_to(ROOT.parent)), "opened": opened,
                     "layers": len(project.mapLayers()),
                     "layouts": len(project.layoutManager().layouts()),
                     "invalid_layers": invalid, "missing_pictures": pictures})
        project.clear()
    result = {"projects": rows, "passed": all(
        r["opened"] and r["layers"] > 0 and r["layouts"] > 0 and
        not r["invalid_layers"] and not r["missing_pictures"] for r in rows)}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"projects": len(rows), "passed": result["passed"],
                      "failures": [r for r in rows if r["invalid_layers"] or r["missing_pictures"]]},
                     ensure_ascii=False))
    project = None
    app.exitQgis()
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
