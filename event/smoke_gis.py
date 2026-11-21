"""Deterministic integration fixture, explicitly NOT an agent performance test."""
import argparse
import json
from pathlib import Path

from gis_client import GISClient
from verify_gis import verify


def run(workspace):
    records = []
    with GISClient(workspace) as client:
        def call(tool, **arguments):
            response = client.call(tool, **arguments)
            records.append({"tool": tool, "arguments": arguments, "response": response})
            if response["status"] != "ok":
                raise RuntimeError(response)
            return response["result"]

        def process(algorithm, output_id, **params):
            return call("process", algorithm="native:"+algorithm, params=params, output_id=output_id)

        def ref(name):
            return {"layer": name}

        call("set_parameters", values={"grid_size": 1000, "road_filter": "TRUE"})
        process("reprojectlayer", "aoi_m", INPUT=ref("aoi"), TARGET_CRS="EPSG:32630")
        process("reprojectlayer", "roads_m", INPUT=ref("roads"), TARGET_CRS="EPSG:32630")
        process("clip", "roads_aoi", INPUT=ref("roads_m"), OVERLAY=ref("aoi_m"))
        process("extractbyexpression", "selected", INPUT=ref("roads_aoi"),
                EXPRESSION={"parameter": "road_filter"})
        process("dissolve", "road_union", INPUT=ref("selected"), FIELD=[])
        process("creategrid", "grid", TYPE=2, EXTENT=ref("aoi_m"), CRS="EPSG:32630",
                HSPACING={"parameter": "grid_size"}, VSPACING={"parameter": "grid_size"})
        process("sumlinelengths", "denominator", LINES=ref("road_union"),
                POLYGONS=ref("grid"), LEN_FIELD="total_m", COUNT_FIELD="road_parts")
        process("clip", "exposed", INPUT=ref("road_union"), OVERLAY=ref("flood"))
        process("sumlinelengths", "lengths", LINES=ref("exposed"),
                POLYGONS=ref("denominator"), LEN_FIELD="exposed_m", COUNT_FIELD="exposed_parts")
        process("fieldcalculator", "result", INPUT=ref("lengths"), FIELD_NAME="ratio",
                FIELD_TYPE=0, FIELD_LENGTH=20, FIELD_PRECISION=10,
                FORMULA='CASE WHEN "total_m" > 0 THEN "exposed_m" / "total_m" ELSE NULL END')
        table = call("table", layer_id="result", fields=["id", "total_m", "exposed_m", "ratio"],
                     sort_by="exposed_m")
        call("export_map", layer_id="result", field="exposed_m",
             title="Flood-exposed road length by 1 km grid", unit="m", output_id="smoke_map",
             breaks=[0, 1000, 3000, 6000, 12000, 30000],
             source_note="Flood: Copernicus EMSR773, 31 Oct 2024.\n"
             "Roads: OpenStreetMap contributors,\n28 Oct 2024 historical snapshot; ODbL.\n"
             "Coincident road segments dissolved.\n"
             "Roads clipped to study boundary.\nGrid origin: AOI upper-left in UTM.",
             decimals=0)
        invalidation = call("set_parameters", values={"grid_size": 2000})
        stale = client.call("table", layer_id="result", fields=["id"])
        assert stale["status"] == "error" and "Stale" in stale["message"]
        assert "smoke_map" in invalidation["invalidated"]
        reference = verify(Path(workspace) / "result.gpkg")
        result = {
            "kind": "deterministic_tool_integration_not_agent_evaluation",
            "reference": reference, "stale_result_blocked": True,
            "stale_map_invalidated": True, "top_rows": table["rows"],
        }
        (Path(workspace) / "verification.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
        if not reference["passed"]:
            raise RuntimeError("Independent numerical verification failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    run(parser.parse_args().workspace)
