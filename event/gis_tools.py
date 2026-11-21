"""QGIS JSON-lines worker: atomic operations and optional dependency tracking."""
import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path

from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsLayoutExporter,
    QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemMap,
    QgsLayoutItemMapGrid, QgsLayoutItemPage, QgsLayoutItemPicture,
    QgsLayoutItemScaleBar, QgsLayoutPoint, QgsLayoutSize, QgsPrintLayout,
    QgsProject, QgsVectorLayer, QgsGraduatedSymbolRenderer, QgsRendererRange,
    QgsFillSymbol, QgsLineSymbol, QgsRectangle, QgsProcessingContext,
)
from qgis.PyQt.QtGui import QColor, QFont

ROOT = Path(__file__).resolve().parent
ALGORITHMS = (
    "native:reprojectlayer", "native:fixgeometries", "native:clip",
    "native:intersection", "native:dissolve", "native:creategrid",
    "native:extractbyexpression", "native:fieldcalculator",
    "native:sumlinelengths", "native:aggregate", "native:countpointsinpolygon",
)


def plain(value):
    if value is None or (hasattr(value, "isNull") and value.isNull()):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class Workspace:
    def __init__(self, directory, tracked=True, resume=False):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.tracked = tracked
        self.layers = {}
        self.parameters = {}
        self.artifacts = {}
        self.events = []
        self.context = QgsProcessingContext()
        # Invalid geometry is an actionable error, never silently omitted.
        from qgis.core import QgsFeatureRequest
        self.context.setInvalidGeometryCheck(QgsFeatureRequest.GeometryAbortOnInvalid)
        checkpoint = self.directory / "worker-checkpoint.json"
        legacy = self.directory / "state.json"
        if resume:
            saved = json.loads((checkpoint if checkpoint.exists() else legacy).read_text())
            if saved.get("tracked", tracked) != tracked:
                raise ValueError("Cannot change experimental condition while resuming")
            self.layers = saved["layers"]
            self.parameters = saved["parameters"]
            self.artifacts = saved["artifacts"]
            for item in self.layers.values():
                actual = hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()
                if actual != item["sha256"]:
                    raise ValueError("A layer changed since checkpoint; cannot resume")
            return
        if legacy.exists():
            raise ValueError("Workspace exists; use --resume or a new output directory")
        for key, path in (
            ("roads", ROOT / "inputs/assets/roads-20241028.geojson"),
            ("aoi", ROOT / "inputs/assets/study-area.geojson"),
            ("flood", ROOT / "products/ems-reference.geojson"),
        ):
            if path.is_file():
                self.register(key, path, [], [], source=True)

    def register(self, key, path, inputs, parameters, source=False):
        if key in self.layers or key in self.artifacts:
            raise ValueError("IDs are immutable; choose a new output_id")
        layer = QgsVectorLayer(str(path), key, "ogr")
        if not layer.isValid() or not layer.crs().isValid():
            raise ValueError("Output layer or coordinate reference system is invalid")
        self.layers[key] = {
            "path": str(path), "inputs": sorted(set(inputs)),
            "parameters": sorted(set(parameters)), "stale": False, "source": source,
            "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        }
        return self.inspect(key)

    def layer(self, key):
        item = self.layers[key]
        if self.tracked and item["stale"]:
            raise ValueError(f"Stale dependency: {key}; recompute from current parameters")
        result = QgsVectorLayer(item["path"], key, "ogr")
        if not result.isValid():
            raise ValueError(f"Cannot load layer: {key}")
        return result

    def inspect(self, layer_id):
        layer = self.layer(layer_id)
        extent = layer.extent()
        return {
            "layer_id": layer_id, "features": layer.featureCount(),
            "crs": layer.crs().authid(), "map_unit": Qgis.DistanceUnit(layer.crs().mapUnits()).name,
            "fields": [{"name": f.name(), "type": f.typeName()} for f in layer.fields()],
            "extent": [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()],
            "sample": [
                {f.name(): plain(feature[f.name()]) for f in layer.fields()}
                for _, feature in zip(range(3), layer.getFeatures())
            ],
        }

    def state(self):
        if self.tracked:
            layers = self.layers
            artifacts = self.artifacts
        else:
            layers = {key: {"path": item["path"]} for key, item in self.layers.items()}
            artifacts = {key: {"path": item["path"]} for key, item in self.artifacts.items()}
        return {"parameters": self.parameters, "layers": layers, "artifacts": artifacts}

    def set_parameters(self, values):
        if not isinstance(values, dict):
            raise ValueError("values must be an object")
        changed = {k for k, v in values.items() if self.parameters.get(k) != v}
        self.parameters.update(values)
        invalidated = []
        # Entries are created only after their inputs, so insertion order is a DAG order.
        for key, item in {**self.layers, **self.artifacts}.items():
            parents = [self.layers.get(k, self.artifacts.get(k)) for k in item["inputs"]]
            if changed.intersection(item["parameters"]) or any(p["stale"] for p in parents):
                item["stale"] = True
                invalidated.append(key)
        return {"changed": sorted(changed),
                "invalidated": invalidated if self.tracked else [],
                "parameters": self.parameters}

    def resolve(self, value, inputs, parameters):
        if isinstance(value, dict):
            if set(value) == {"layer"}:
                key = value["layer"]
                inputs.add(key)
                return self.layer(key)
            if set(value) == {"parameter"}:
                key = value["parameter"]
                parameters.add(key)
                return self.parameters[key]
            return {k: self.resolve(v, inputs, parameters) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v, inputs, parameters) for v in value]
        return value

    def algorithm_help(self, algorithm):
        if algorithm not in ALGORITHMS:
            raise ValueError("Algorithm is outside the declared tool set")
        alg = QgsApplication.processingRegistry().algorithmById(algorithm)
        return {
            "algorithm": algorithm, "description": alg.shortHelpString(),
            "parameters": [
                {"name": p.name(), "description": p.description(), "type": p.type(),
                 "default": plain(p.defaultValue()),
                 "options": p.options() if hasattr(p, "options") else None}
                for p in alg.parameterDefinitions()
            ],
            "outputs": [o.name() for o in alg.outputDefinitions()],
        }

    def process(self, algorithm, params, output_id):
        import processing
        if algorithm not in ALGORITHMS:
            raise ValueError("Algorithm is outside the declared tool set")
        if not output_id.replace("_", "").isalnum() or output_id in self.layers:
            raise ValueError("Use a new alphanumeric output_id")
        if "OUTPUT" in params:
            raise ValueError("OUTPUT is generated by the worker")
        inputs, parameters = set(), set()
        resolved = self.resolve(params, inputs, parameters)
        # Layer paths must be registered handles, not arbitrary file access.
        alg = QgsApplication.processingRegistry().algorithmById(algorithm)
        for definition in alg.parameterDefinitions():
            key = definition.name()
            if definition.type() in ("source", "vector", "maplayer", "multilayer"):
                value = resolved.get(key)
                if value is not None and not isinstance(value, QgsVectorLayer):
                    raise ValueError(f"{key} requires a registered layer reference")
        layers = [v for v in resolved.values() if isinstance(v, QgsVectorLayer)]
        if algorithm in ("native:clip", "native:intersection", "native:sumlinelengths",
                         "native:countpointsinpolygon"):
            if len({v.crs().authid() for v in layers}) > 1:
                raise ValueError("CRS mismatch: explicitly reproject inputs before overlay")
        if algorithm in ("native:sumlinelengths", "native:creategrid"):
            crs = (QgsCoordinateReferenceSystem(str(resolved["CRS"]))
                   if algorithm.endswith("creategrid") else layers[0].crs())
            if crs.mapUnits() != Qgis.DistanceUnit.Meters:
                raise ValueError("Metric measurement/grid requires projected metre CRS")
        target = self.directory / f"{output_id}.gpkg"
        resolved["OUTPUT"] = str(target)
        result = processing.run(algorithm, resolved, context=self.context)
        metadata = self.register(output_id, result["OUTPUT"], inputs, parameters)
        self.layers[output_id]["operation"] = {"algorithm": algorithm, "params": params}
        return metadata

    def table(self, layer_id, fields, sort_by=None, descending=True, limit=10):
        layer = self.layer(layer_id)
        for field in fields + ([sort_by] if sort_by else []):
            if layer.fields().indexOf(field) < 0:
                raise ValueError(f"Unknown field: {field}")
        rows = [{f: plain(feature[f]) for f in set(fields + ([sort_by] if sort_by else []))}
                for feature in layer.getFeatures()]
        if sort_by:
            nonnull = [row for row in rows if row[sort_by] is not None]
            empty = [row for row in rows if row[sort_by] is None]
            rows = sorted(nonnull, key=lambda r: r[sort_by], reverse=descending) + empty
        totals = {field: sum(row[field] for row in rows
                             if isinstance(row[field], (float, int))) for field in fields}
        return {"rows": rows[:min(max(int(limit), 1), 50)],
                "row_count": len(rows), "numeric_sums": totals}

    def export_map(self, layer_id, field, title, unit, output_id,
                   breaks, source_note, decimals=1):
        layer = self.layer(layer_id)
        if layer.fields().indexOf(field) < 0:
            raise ValueError("Map field not found")
        if output_id in self.artifacts or not output_id.replace("_", "").isalnum():
            raise ValueError("Use a new alphanumeric output_id")
        if len(breaks) < 3 or len(breaks) > 7 or sorted(set(breaks)) != breaks:
            raise ValueError("Provide 3-7 strictly increasing numeric class boundaries")
        vals = [plain(f[field]) for f in layer.getFeatures()]
        vals = [v for v in vals if v is not None]
        if not vals or min(vals) < breaks[0] or max(vals) > breaks[-1]:
            raise ValueError("Class boundaries must cover all observed non-null values")
        project = QgsProject()
        project.setCrs(layer.crs())
        project.setFilePathStorage(Qgis.FilePathType.Relative)
        colors = ["#fff4b2", "#f6ce68", "#ed9850", "#d65e43", "#a92742", "#651d3f"]
        ranges = []
        for i, (low, high) in enumerate(zip(breaks, breaks[1:])):
            symbol = QgsFillSymbol.createSimple({
                "color": colors[round(i * (len(colors)-1) / (len(breaks)-2))],
                "outline_color": "#ffffff", "outline_width": ".18",
            })
            ranges.append(QgsRendererRange(low, high, symbol,
                          f"{low:.{decimals}f} - {high:.{decimals}f} {unit}"))
        layer.setName(f"{field} ({unit}); blank = no data")
        layer.setRenderer(QgsGraduatedSymbolRenderer(field, ranges))
        project.addMapLayer(layer)
        border = self.layer("aoi")
        border.setName("Study boundary")
        border.renderer().setSymbol(QgsFillSymbol.createSimple({
            "style": "no", "outline_color": "#353535", "outline_width": ".35",
        }))
        project.addMapLayer(border)
        flood = self.layer("flood")
        flood.setName("EMS flood boundary, 31 Oct 2024")
        flood.renderer().setSymbol(QgsFillSymbol.createSimple({
            "style": "no", "outline_color": "#126d92", "outline_width": ".22",
        }))
        project.addMapLayer(flood)
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()
        layout.setName(output_id)
        layout.pageCollection().pages()[0].setPageSize("A3", QgsLayoutItemPage.Landscape)
        layout.renderContext().setDpi(180)

        def label(text, x, y, w, h, size):
            item = QgsLayoutItemLabel(layout)
            item.setText(text)
            item.setFont(QFont("Helvetica", size))
            layout.addLayoutItem(item)
            item.attemptMove(QgsLayoutPoint(x, y))
            item.attemptResize(QgsLayoutSize(w, h))
            return item

        label(title, 12, 7, 395, 13, 20)
        label("HORTA SUD | VALENCIA | ROAD EXPOSURE TO MAPPED FLOOD EXTENT", 12, 22, 395, 8, 10)
        main = QgsLayoutItemMap(layout)
        layout.addLayoutItem(main)
        main.attemptMove(QgsLayoutPoint(26, 40))
        main.attemptResize(QgsLayoutSize(261, 232))
        main.setCrs(layer.crs())
        main.setLayers([border, flood, layer])
        main.setKeepLayerSet(True)
        main.setFrameEnabled(True)
        extent = QgsRectangle(layer.extent())
        extent.scale(1.04)
        main.zoomToExtent(extent)
        grid = main.grid()
        grid.setEnabled(True)
        grid.setIntervalX(2000)
        grid.setIntervalY(2000)
        grid.setStyle(QgsLayoutItemMapGrid.FrameAnnotationsOnly)
        grid.setAnnotationEnabled(True)
        grid.setAnnotationPrecision(0)
        grid.setAnnotationFont(QFont("Helvetica", 7))
        grid.setFrameStyle(QgsLayoutItemMapGrid.ExteriorTicks)
        grid.setAnnotationDisplay(QgsLayoutItemMapGrid.HideAll, QgsLayoutItemMapGrid.Right)
        legend = QgsLayoutItemLegend(layout)
        legend.setTitle("LEGEND")
        legend.setLinkedMap(main)
        layout.addLayoutItem(legend)
        legend.attemptMove(QgsLayoutPoint(303, 42))
        legend.setAutoUpdateModel(False)
        legend.model().rootGroup().clear()
        for visible in (layer, flood, border):
            legend.model().rootGroup().addLayer(visible)
        legend.setWrapString(";")
        scale = QgsLayoutItemScaleBar(layout)
        scale.setStyle("Single Box")
        scale.setLinkedMap(main)
        scale.setUnits(Qgis.DistanceUnit.Kilometers)
        scale.setUnitsPerSegment(1)
        scale.setNumberOfSegments(2)
        scale.setNumberOfSegmentsLeft(0)
        scale.setUnitLabel("km")
        layout.addLayoutItem(scale)
        scale.attemptMove(QgsLayoutPoint(304, 145))
        north = QgsLayoutItemPicture(layout)
        layout.addLayoutItem(north)
        north.attemptMove(QgsLayoutPoint(381, 122))
        north.attemptResize(QgsLayoutSize(14, 23))
        north.setPicturePath(str(ROOT / "cartography_data/north.svg"))
        label("N (grid)", 381, 114, 21, 7, 8)
        label("METHOD AND SOURCES", 303, 167, 100, 8, 10)
        label(source_note, 303, 179, 102, 56, 9)
        label(f"{layer.crs().authid()} | WGS 84 / UTM zone 30N\n"
              "Grid north; metric planar lengths.\n"
              "Exposure does not establish damage,\nroad closure, or flood depth.",
              303, 240, 102, 31, 9)
        label("Course research map | Source geometries retained | Not an emergency certification",
              14, 281, 390, 7, 8)
        project.layoutManager().addLayout(layout)
        path = self.directory / f"{output_id}.qgz"
        if not project.write(str(path)):
            raise RuntimeError("Cannot write QGIS project")
        exporter = QgsLayoutExporter(layout)
        pdf = path.with_suffix(".pdf")
        png = path.with_suffix(".png")
        if exporter.exportToPdf(str(pdf), QgsLayoutExporter.PdfExportSettings()) != QgsLayoutExporter.Success:
            raise RuntimeError("PDF export failed")
        image_settings = QgsLayoutExporter.ImageExportSettings()
        image_settings.dpi = 150
        if exporter.exportToImage(str(png), image_settings) != QgsLayoutExporter.Success:
            raise RuntimeError("Image export failed")
        self.artifacts[output_id] = {
            "path": str(path), "pdf": str(pdf), "png": str(png), "inputs": [layer_id],
            "parameters": [], "stale": False,
            "spec": {"layer_id": layer_id, "field": field, "title": title, "unit": unit,
                     "breaks": breaks, "source_note": source_note},
        }
        return self.artifacts[output_id]

    def view_map(self, artifact_id):
        item = self.artifacts[artifact_id]
        if self.tracked and item["stale"]:
            raise ValueError("Map is stale; recompute and re-export")
        return {"png": item["png"], "spec": item["spec"]}

    def call(self, tool, arguments):
        methods = {
            "state": self.state, "inspect": self.inspect, "set_parameters": self.set_parameters,
            "algorithm_help": self.algorithm_help, "process": self.process,
            "table": self.table, "export_map": self.export_map, "view_map": self.view_map,
        }
        try:
            result = {"status": "ok", "result": methods[tool](**arguments)}
        except Exception as exc:
            result = {"status": "error", "type": type(exc).__name__, "message": str(exc)}
        self.events.append({"tool": tool, "arguments": arguments, "response": result})
        (self.directory / "state.json").write_text(json.dumps(self.state(), ensure_ascii=False, indent=2))
        (self.directory / "worker-checkpoint.json").write_text(json.dumps({
            "layers": self.layers, "parameters": self.parameters, "artifacts": self.artifacts,
            "tracked": self.tracked,
        }, ensure_ascii=False, indent=2))
        with (self.directory / "tools.jsonl").open("a") as stream:
            stream.write(json.dumps(self.events[-1], ensure_ascii=False) + "\n")
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--untracked", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    sys.path.append(str(Path(os.environ["QGIS_PREFIX_PATH"]) / "share/qgis/python/plugins"))
    from processing.core.Processing import Processing
    Processing.initialize()
    workspace = Workspace(args.workspace, tracked=not args.untracked, resume=args.resume)
    print(json.dumps({"ready": True, "catalog": [workspace.inspect(k) for k, v in workspace.layers.items()
                                               if v.get("source")],
                      "algorithms": ALGORITHMS}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            result = workspace.call(request["tool"], request.get("arguments", {}))
        except Exception as exc:
            result = {"status": "error", "message": str(exc)}
        print(json.dumps(result, ensure_ascii=False), flush=True)
    # Some macOS Qt/QGIS builds fault during finalizer ordering after headless use.
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
