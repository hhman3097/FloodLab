"""Live Pi / Responses test against a running local preview; key stays in memory."""
import getpass
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parent
BASE="http://127.0.0.1:8768"
def req(route,body=None):
    with urlopen(Request(BASE+route,data=json.dumps(body).encode() if body is not None else None,
                         headers={"Content-Type":"application/json"}),timeout=120) as r:return json.load(r)

def ask(question,**kw):
    job=req("/api/task",{"question":question,**kw})
    for _ in range(240):
        status=next(j for j in req("/api/status")["jobs"] if j["id"]==job["job_id"])
        if status["status"]=="finished":return req("/api/run?"+urlencode({"id":job["run_id"]}))
        if status["status"]=="failed":raise RuntimeError(status.get("error"))
        time.sleep(1)
    raise TimeoutError("Live test timed out")

def main():
    req("/api/connection",{"key":getpass.getpass("Test key: "),"model":"gpt-6.2-sol"})
    try:
        parent=ROOT/"qa"/"用户工作区";parent.mkdir(exist_ok=True)
        key=req("/api/folder",{"parent":str(parent)})["key"]
        a=ask("我想分析2026年10月希腊克里特岛的洪涝。先查实际可用的观测产品，只告诉我 Kastelli 这一区的观测日期，先别算。",folder_key=key)
        b=ask("那就分析这个区吧，算一下覆盖了多大面积，哪几个1公里网格最集中，给我地图。",run_id=a["id"])
        assert a["project"]==b["project"] and b["event_outputs"]
        o=b["event_outputs"][-1]
        assert o["aoi"]=="Kastelli" and abs(o["total_km2"]-.24126593)<1e-6
        with urlopen(BASE+"/api/project-output?"+urlencode({"run":b["id"],"folder":o["folder"],"file":"map.pdf"})) as pdf:assert pdf.read(4)==b"%PDF"
        fresh=ask("我手头没有数据，先告诉我有哪些方向，不要下载或计算。",folder_key=key)
        assert fresh["project"]["root"]!=a["project"]["root"] and not fresh["event_outputs"]
        assert req("/api/run?"+urlencode({"id":b["id"]}))["project"]==b["project"]
        (ROOT/"qa/project-check.json").write_text(json.dumps({"project":b["project"],"result":o,"run":b["id"],
                     "separate":fresh["project"],"calls":[c["name"] for t in b["turns"] for c in t["calls"]]},indent=2,ensure_ascii=False))
        print("PASS",b["id"],o["total_km2"],b["project"]["root"])
    finally:req("/api/disconnect",{})

if __name__=="__main__":main()
