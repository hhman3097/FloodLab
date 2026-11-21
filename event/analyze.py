"""Valencia event analysis. The reference is not used to set detection parameters."""
import csv
import json
import sys
from pathlib import Path

import numpy as np
import rasterio as rio
from rasterio.features import rasterize, shapes
from scipy.ndimage import median_filter
from shapely import make_valid, transform, wkt
from shapely.geometry import box, mapping, shape
from pyproj import Transformer
from skimage.morphology import remove_small_objects

from acquire import RAW, ROOT, CRS, RES, save

sys.path.insert(0, str(ROOT.parent))
from floodlab import FCN, scores
from skimage.filters import threshold_otsu

OUT = ROOT / "products"
DATES = ("2024-10-08", "2024-10-20", "2024-11-01")
CELL = RES ** 2 / 1e6


def emit(name, content):
    (OUT / name).write_text(json.dumps(content, indent=2, ensure_ascii=False, allow_nan=False))


def read(path):
    with rio.open(path) as ds:
        return ds.read(), ds.profile


def mask_file(name, mask, valid, profile):
    p = {k:v for k,v in profile.items() if k not in ("count","dtype","nodata","interleave")}
    save(OUT / f"{name}.tif", np.where(valid, mask, 255).astype("uint8"), p, 255)


def vectors(name, geom_props):
    emit(f"{name}.geojson", {
        "type": "FeatureCollection", "name": name,
        "crs":{"type":"name","properties":{"name":"urn:ogc:def:crs:EPSG::32630"}},
        "features": [{"type":"Feature","geometry": g, "properties": p}
                     for g,p in geom_props],
    })


def polygons(name, valid, affine):
    vectors(name, ((g, {"area_ha": shape(g).area / 10000, "class": name})
                   for g,v in shapes(valid.astype("uint8"), mask=valid, transform=affine)))


def model_map(model, sar, valid):
    height, width = valid.shape
    mask = np.zeros_like(valid)
    for y in range(0, height, 512):
        for x in range(0, width, 512):
            tile = sar[:, y:y+512, x:x+512]
            footprint = valid[y:y+512, x:x+512]
            h,w = footprint.shape
            tile = np.pad(tile, ((0,0),(0,512-h),(0,512-w)), mode="edge")
            support = np.pad(footprint, ((0,512-h),(0,512-w)), mode="edge")
            mask[y:y+h, x:x+w] = model.predict(tile, support)[:h,:w]
    return mask & valid


def change(baseline, after, valid, vv, vh, drop, minimum):
    delta = baseline[0] - after[0]
    mask = valid & (after[0] < vv) & (after[1] < vh) & (delta > drop)
    return remove_small_objects(mask, max_size=minimum-1, connectivity=2) if minimum > 1 else mask


def compare(pred, ref, support):
    p,r = pred[support], ref[support]
    tp,fp,fn,tn = map(int, [(p&r).sum(), (p&~r).sum(), (~p&r).sum(), (~p&~r).sum()])
    return dict(tp=tp,fp=fp,fn=fn,tn=tn,**scores(tp,fp,fn,tn),
                prediction_km2=(tp+fp)*CELL, reference_km2=(tp+fn)*CELL)


def main():
    OUT.mkdir(exist_ok=True)
    provenance = json.loads((RAW/"provenance.json").read_text())
    inputs = [read(RAW/f"sar-{d}-r110.tif") for d in DATES]
    sar = [a for a,p in inputs]
    profile = inputs[0][1]
    assert all(p["transform"] == profile["transform"] and p["crs"] == profile["crs"] and
               p["width"] == profile["width"] and p["height"] == profile["height"] for _,p in inputs)
    footprint = [np.isfinite(a).all(axis=0) for a in sar]
    common = np.logical_and.reduce(footprint)
    bounds = rio.transform.array_bounds(*common.shape, profile["transform"])
    transformer = Transformer.from_crs(4326, CRS, always_xy=True)
    def project(geometry):
        return transform(geometry, transformer.transform, interleaved=False)
    extent = box(*bounds)
    affine = profile["transform"]
    def burn(geoms):
        return rasterize(((g,1) for g in geoms if not g.is_empty), out_shape=common.shape,
                         transform=affine, fill=0, dtype="uint8").astype(bool)
    # Land domain: the west side of the lagoon and coastal plain is within the EMS AOI.
    # Ocean pixels are removed with a deliberately fixed coast-side AOI, not the flood polygons.
    coast = [(-.65,39.22),(-.315,39.22),(-.310,39.27),(-.299,39.32),(-.295,39.36),
             (-.306,39.40),(-.319,39.44),(-.317,39.48),(-.314,39.52),(-.65,39.52),(-.65,39.22)]
    domain_geom = project(shape({"type":"Polygon","coordinates":[coast]})).intersection(extent)
    domain = burn([domain_geom])
    valid = common & domain
    clean = [np.stack([median_filter(np.where(np.isfinite(b), b, -6.), size=3) for b in a])
             for a in sar]
    b0,b1,post = clean
    pre = (b0+b1)/2
    default = {"vv": -16, "vh": -23, "drop": 3, "minimum": 12}
    flood = change(pre, post, valid, **default)
    pseudo = change(b0, b1, valid, **default)
    otsu_t = float(threshold_otsu(sar[-1][1][valid]))
    otsu = (sar[-1][1] <= otsu_t) & valid
    fcn = model_map(FCN(), sar[-1], valid)
    prior = valid & (b0[0]<default["vv"]) & (b0[1]<default["vh"]) & (
        b1[0]<default["vv"]) & (b1[1]<default["vh"])
    for name,m in [("candidate", flood),("placebo", pseudo),("otsu-water", otsu),
                   ("fcn-water", fcn),("stable-water", prior)]:
        mask_file(name,m,valid,profile)
    for name,m in [("candidate",flood),("stable-water",prior),("unobserved",domain&~common)]:
        polygons(name,m,affine)
    vectors("study-area",[(mapping(domain_geom),{"name":"Study domain"})])
    print("Prediction frozen; scoring against asynchronous EMS evidence.", flush=True)
    ems_geom = project(wkt.loads(provenance["ems"]["extent_wkt"])).intersection(domain_geom)
    extent_mask = burn([ems_geom])
    raw = json.loads((RAW/"ems-delineation.geojson").read_text())
    ref_geom = [make_valid(project(shape(f["geometry"]))).intersection(ems_geom)
                for f in raw["features"] if f["geometry"] and f["properties"]["notation"]=="Flooded area"]
    ref_geom = [g for g in ref_geom if g.area>0]
    ref = burn(ref_geom)
    vectors("ems-reference",[(mapping(g),{"area_ha":g.area/10000}) for g in ref_geom])
    support = valid & extent_mask
    difference = np.where(ref, np.where(flood,1,3),np.where(flood,2,0))
    mask_file("agreement",difference,support,profile)
    for name,k in [("agreement-both",1),("agreement-sar",2),("agreement-ems",3)]:
        polygons(name, (difference==k)&support, affine)
    variants=[]
    for vv in (-18,-16,-14):
        for drop in (2,3,4):
            options = dict(vv=vv,vh=-23,drop=drop,minimum=12)
            m = change(pre,post,valid,**options)
            n = change(b0,b1,valid,**options)
            variants.append(dict(vv=vv,drop=drop,area_km2=float(m.sum()*CELL),
                                 placebo_km2=float(n.sum()*CELL), **compare(m,ref,support)))
    with (OUT/"sensitivity.csv").open("w",newline="") as s:
        writer=csv.DictWriter(s,fieldnames=list(variants[0]))
        writer.writeheader()
        writer.writerows(variants)
    metrics = {key: compare(m,ref,support) for key,m in
               [("otsu_post_water",otsu),("fcn_post_water",fcn),("change",flood),
                ("otsu_minus_stable",otsu&~prior),("fcn_minus_stable",fcn&~prior)]}
    dem,_=read(RAW/"terrain.tif")
    vals=dem[0][flood&np.isfinite(dem[0])]
    strict = change(pre, post, valid, vv=-18, vh=-23, drop=4, minimum=12)
    broad = change(pre, post, valid, vv=-14, vh=-23, drop=2, minimum=12)
    mask_file("parameter-support", np.where(strict,2,np.where(broad,1,0)), valid, profile)
    dem_classes = [
        {"range": name,"km2": float(zone.sum()*CELL),"metrics": compare(flood,ref,zone)}
        for name, z in [
            ("<2 m",dem[0]<2),("2-10 m",(dem[0]>=2)&(dem[0]<10)),(">=10 m",dem[0]>=10)]
        if (zone := z & support).any()]
    qa = {"dates":DATES,"orbit":110,"crs":CRS,"resolution_m":RES,
          "domain_km2":float(domain.sum()*CELL), "common_km2":float(valid.sum()*CELL),
          "coverage":float(valid.sum()/domain.sum()),"comparison_domain_km2":float(support.sum()*CELL),
          "params":default,"otsu_db":otsu_t,"candidate_km2":float(flood.sum()*CELL),
          "placebo_km2":float(pseudo.sum()*CELL),"stable_dark_km2":float(prior.sum()*CELL),
          "candidate_elevation_p50_m":float(np.median(vals)) if vals.size else None,
          "candidate_elevation_p95_m":float(np.quantile(vals,.95)) if vals.size else None,
          "parameter_support_km2":{"all_nine":float(strict.sum()*CELL),
                                   "one_or_more":float(broad.sum()*CELL)},
          "terrain_strata":dem_classes,
          "reference_time":provenance["ems"]["image"]["acquisitionTime"],
          "reference_semantics":"asynchronous comparison, not ground truth",
          "metrics":metrics}
    emit("analysis.json",qa)
    print(json.dumps({k:qa[k] for k in ("coverage","candidate_km2","placebo_km2","metrics")}),flush=True)


if __name__ == "__main__":
    main()
