"""Discover and acquire only official observed-event polygons, scoped to a chat."""
import hashlib
import json
import re
import subprocess
import uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,urlopen
from runtime import executable,environment

BASE="https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/"


def download(url,limit):
    with urlopen(Request(url,headers={"User-Agent":"FloodLab-Research/0.6"}),timeout=30) as r:
        data=r.read(limit+1)
    if len(data)>limit:raise ValueError("产品超过本地下载限额")
    return data


def match_events(query,from_date,to_date):
    url=BASE+"public-activations-info/?limit=1000"
    rows=json.loads(download(url,5_000_000))["results"]
    terms=re.findall(r"[\w-]+",query.casefold())
    hits=[]
    for r in rows:
        if r["category"].lower()!="flood":continue
        day=r["eventTime"][:10]
        if from_date and day<from_date or to_date and day>to_date:continue
        label=" ".join([r["code"],r["name"],*r["countries"]]).casefold()
        if terms and not all(t in label for t in terms):continue
        hits.append({"code":r["code"],"name":r["name"],"countries":r["countries"],"date":day})
    return sorted(hits,key=lambda r:r["date"],reverse=True)[:15]


def inventory(code):
    if not re.fullmatch(r"EMSR[0-9]{3,5}",code):raise ValueError("无效事件编号")
    url=BASE+"public-activations/?"+urlencode({"code":code})
    entries=json.loads(download(url,12_000_000)).get("results",[])
    r=next((r for r in entries if r["code"]==code and r["category"].casefold()=="flood"),None)
    if not r:return []
    out=[]
    for a in r["aois"]:
        for p in a["products"]:
            for layer in p.get("layers",[]):
                link=layer.get("json","");u=urlsplit(link)
                if u.scheme!="https" or u.hostname!="rapidmapping-viewer.s3.eu-west-1.amazonaws.com" or not u.path.startswith("/"+code+"/"):continue
                if not re.search(r"_observedEventA_v\d+\.json$",u.path,re.I):continue
                out.append({"key":code+":"+str(p["id"]),"event":r["name"],"code":code,"event_date":r["eventTime"][:10],
                            "aoi":a["name"],"aoi_number":a["number"],"product_id":p["id"],"source":link,"catalog":url,
                            "observations":sorted({i["acquisitionTime"] for i in p.get("images",[]) if i.get("new") and i.get("acquisitionTime")}),
                            "version":p["version"]})
    return out


def put(path,obj):
    tmp=path.with_name(uuid.uuid4().hex+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding="utf-8");tmp.replace(path)


class EventTools:
    def __init__(self,root):
        self.root=Path(root)
        self.state=self.root/"data-index.json"
        self.index=json.loads(self.state.read_text(encoding="utf-8")) if self.state.exists() else {"products":{},"catalog":{},"outputs":[]}
        self.audit=[]

    def call(self,name,args):
        if name=="find_events":
            result=match_events(str(args.get("place","")),args.get("from_date"),args.get("to_date"))
        elif name=="list_products":
            result=inventory(str(args["code"]).upper())
            self.index["catalog"].update({p["key"]:p for p in result});put(self.state,self.index)
        elif name=="get_footprint":
            key=args["key"];p=self.index["catalog"].get(key)
            if not p:raise ValueError("须先列出该事件的观测产品")
            folder=self.root/"inputs"/key.replace(":","-");folder.mkdir(parents=True,exist_ok=True)
            path=folder/"observed.geojson"
            cached=key in self.index["products"] and path.exists()
            if cached:
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=self.index["products"][key]["sha256"]:raise ValueError("本地数据校验失败")
            else:raw=download(p["source"],40_000_000)
            fc=json.loads(raw);features=fc.get("features",[])
            if fc.get("type")!="FeatureCollection" or not features or any((f.get("geometry") or {}).get("type") not in ("Polygon","MultiPolygon") for f in features):
                raise ValueError("此产品没有可计算的观测多边形")
            if not cached:
                if path.exists() and path.read_bytes()!=raw:raise ValueError("检测到未登记且内容不同的观测文件，已保留原文件；请选用新工作区")
                if not path.exists():
                    temp=folder/(uuid.uuid4().hex+".tmp")
                    try:temp.write_bytes(raw);temp.replace(path)
                    finally:temp.unlink(missing_ok=True)
            result={**p,"file":str(path.relative_to(self.root)),"features":len(features),"sha256":hashlib.sha256(raw).hexdigest(),
                    "retrieved_utc":self.index["products"][key]["retrieved_utc"] if cached else datetime.now(timezone.utc).isoformat(),"notation":sorted({str(f.get("properties",{}).get("notation","")) for f in features})}
            self.index["products"][key]=result;put(folder/"provenance.json",result);put(self.state,self.index)
        elif name=="analyse_footprint":
            key=args["key"];p=self.index["products"].get(key)
            if not p:raise ValueError("须先下载并核验观测产品")
            grid=int(args.get("grid_m") or 1000)
            if grid not in (500,1000,2000,5000):raise ValueError("网格须为 500、1000、2000 或 5000 米")
            source=self.root/p["file"]
            if hashlib.sha256(source.read_bytes()).hexdigest()!=p["sha256"]:raise ValueError("源文件哈希不匹配")
            out=self.root/"outputs"/(key.replace(":","-")+"-"+uuid.uuid4().hex[:8]);out.mkdir(parents=True)
            proc=subprocess.run([str(executable("python")),str(Path(__file__).with_name("footprint_worker.py")),
                str(source),str(out),str(grid)],env=environment(),capture_output=True,timeout=150)
            if proc.returncode:raise RuntimeError(proc.stderr.decode(errors="replace")[-900:])
            result=json.loads((out/"results.json").read_text(encoding="utf-8"))
            result.update(key=key,aoi=p["aoi"],observations=p["observations"],folder=out.relative_to(self.root).as_posix())
            self.index["outputs"].append(result);put(self.state,self.index)
        else:raise ValueError("未知项目工具")
        self.audit.append({"name":name,"arguments":json.dumps(args,ensure_ascii=False),"response":{"status":"ok","result":result}})
        return result
