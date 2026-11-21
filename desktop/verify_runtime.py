"""Exercise new QGIS calculations in the installed, relocated desktop runtime."""
import json
import sys
from pathlib import Path

workspace = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(workspace / "event"))
from gis_client import GISClient

destination = workspace / "event/runs/desktop-runtime-check"
results = []
with GISClient(destination) as gis:
    def call(tool, **arguments):
        value = gis.call(tool, **arguments)
        results.append({"tool": tool, "arguments": arguments, "result": value})
        assert value["status"] == "ok", value
        return value
    call("state")
    call("process", algorithm="native:reprojectlayer",
         params={"INPUT": {"layer": "roads"}, "TARGET_CRS": "EPSG:32630"}, output_id="desktop_roads_m")
    call("inspect", layer_id="desktop_roads_m")
    call("process", algorithm="native:reprojectlayer",
         params={"INPUT": {"layer": "aoi"}, "TARGET_CRS": "EPSG:32630"}, output_id="desktop_aoi_m")
    call("process", algorithm="native:creategrid", params={
        "TYPE": 2, "EXTENT": {"layer": "desktop_aoi_m"}, "HSPACING": 2000,
        "VSPACING": 2000, "CRS": "EPSG:32630"}, output_id="desktop_grid")
    call("export_map", layer_id="desktop_grid", field="id", title="FloodLab Desktop Runtime Check",
         unit="Grid ID", output_id="desktop_map", breaks=[0, 10, 20, 30, 50],
         source_note="QGIS offline runtime validation; grid IDs are not flood exposure.")
(destination / "runtime-check.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
assert (destination / "desktop_map.pdf").read_bytes().startswith(b"%PDF")
print(json.dumps({"passed": True, "operations": len(results), "workspace": str(destination)}))
