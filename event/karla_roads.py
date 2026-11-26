"""Archive major road geometry from tiled OpenStreetMap map API responses."""
import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary"}


def extract(input_dir, output):
    nodes, ways, sources = {}, {}, []
    for file in sorted(input_dir.glob("*.osm")):
        source = ET.parse(file).getroot()
        if source.tag != "osm":
            raise ValueError("Invalid OSM response")
        extent = source.find("bounds").attrib
        sources.append({"bbox":[float(extent[k]) for k in ("minlon","minlat","maxlon","maxlat")],
                        "sha256":hashlib.sha256(file.read_bytes()).hexdigest()})
        for node in source.findall("node"):
            nodes[(node.get("id"), node.get("version"))] = (float(node.get("lon")),float(node.get("lat")))
        for way in source.findall("way"):
            tags={t.get("k"):t.get("v") for t in way.findall("tag")}
            if tags.get("highway","").removesuffix("_link") not in CLASSES:
                continue
            row=(int(way.get("version")),way.get("timestamp"),tags,[p.get("ref") for p in way.findall("nd")])
            key=way.get("id")
            if key not in ways or row[0]>ways[key][0]:
                ways[key]=row
    if len(sources)!=9:
        raise ValueError("The complete 3 x 3 API window is required")
    latest={}
    for (node,version),xy in nodes.items():
        if node not in latest or int(version)>latest[node][0]:
            latest[node]=(int(version),xy)
    features = []
    for key, (_, date, tags, nds) in sorted(ways.items(),key=lambda item:int(item[0])):
        if len(nds)<2 or not all(n in latest for n in nds):
            raise ValueError("Missing nodes for selected road")
        keep={k:tags[k] for k in ("highway","name","ref","bridge","tunnel") if k in tags}
        keep.update({"@osmId":"way/"+key,"edited_utc":date})
        features.append({"type":"Feature","properties":keep,
                         "geometry":{"type":"LineString","coordinates":[latest[n][1] for n in nds]}})
    output.write_text(json.dumps({"type":"FeatureCollection","features":features},ensure_ascii=False,separators=(",",":")),encoding="utf-8")
    output.with_name("roads-source.json").write_text(json.dumps({
        "dataset":"OpenStreetMap map API", "endpoint":"https://api.openstreetmap.org/api/0.6/map",
        "snapshot":"present-day snapshot retrieved 2026-11-25, not a 2023 network",
        "license":"ODbL; OpenStreetMap contributors", "windows":sources, "features":len(features),
        "roads_sha256":hashlib.sha256(output.read_bytes()).hexdigest()},indent=2),encoding="utf-8")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("source",type=Path)
    parser.add_argument("output",type=Path)
    a=parser.parse_args()
    extract(a.source,a.output)
