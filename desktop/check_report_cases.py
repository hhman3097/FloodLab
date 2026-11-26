"""Replay report showcases with actual Pi, public catalogs and QGIS in isolated workspaces."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import getpass
import json
import os
from pathlib import Path
import shutil
import sys
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"workbench"))
import server

SCENARIOS={
    "putian":[
        "查福建莆田和仙游这次洪涝的卫星观测资料。时间是中国当地时间 2026 年 9 月 3 日到 9 月 6 日。EMS 已经查过没有成品，请直接查 Sentinel-1 和 Sentinel-2 的目录，并给我覆盖图。不要把足迹当洪水范围。",
        "你接着查，然后分析出图。如果没有能确定洪水边界的影像，不要猜面积，解释已经出的图是什么。",
    ],
    "crete":[
        "我想查 2026 年 10 月希腊克里特岛洪涝。查 EMSR933 的 Kastelli 研究区有哪些可用观测日期，先别下载或计算。",
        "那就分析刚才的 Kastelli。用现有的官方观测产品，算合并面积、排出 1 公里网格的前五名，并出图。",
        "第一名的百分比是整个克里特岛的比例吗？解释一下就好，不要重算。",
        "换成海南保亭，不联网、不下载、不计算。只说如果要做道路影响分析，还需要什么数据，不要附原地图。",
        "回到 Kastelli，那张已经做好的地图给我再看一眼，不要重新下载或计算。",
    ],
    "valencia":[
        "沿用预置的瓦伦西亚 Horta Sud 案例。只看住宅道路（residential），改为 500 米网格，做洪涝暴露率地图。请实际计算总长、暴露长、分母和前五名。",
        "这张图是不是能告诉我哪几条路一定封了、积水最深有多深？只解释证据，不要重算。",
    ],
}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("output",type=Path)
    parser.add_argument("--case",choices=SCENARIOS,action="append")
    args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    server.RUNS=out/"runs";server.RUNS.mkdir()
    key=os.getenv("AI4S_API_KEY") or getpass.getpass("Responses test key (not stored): ")
    def execute(item):
        idx,(name,questions)=item
        api=server.API("https://api.clawlab.chat/v1",key,"gpt-6.2-sol")
        records=[];previous=None;projects=server.Projects(server.RUNS)
        for step,q in enumerate(questions,1):
            run_id=f"workbench_{idx:05x}{step:05x}"
            directory,_=projects.bind(run_id,previous,None)
            if previous is None:
                (directory/"dialogue.json").write_text('{"turns":[]}',encoding="utf-8")
            else:
                prev=projects.directory(previous)
                for filename in ("dialogue.json","agent-run.json","worker-checkpoint.json","state.json"):
                    if (prev/filename).exists():shutil.copy2(prev/filename,directory/filename)
            server.JOBS[run_id]={}
            server.worker(run_id,api,directory,q,bool(previous),threading.Event())
            summary=server.summary(run_id);last=summary["turns"][-1]
            row={"question":q,"job":server.JOBS[run_id]["status"],"answer":last.get("answer"),
                 "failure":server.JOBS[run_id].get("error") or next((t.get("error") or t.get("answer") for t in reversed(summary["turns"]) if t["status"] in {"failed","incomplete"}),None),
                 "calls":[(c["name"],c["response"]["status"]) for c in last["calls"]],
                 "artifacts":summary["artifacts"],"event_outputs":summary["event_outputs"],
                 "project":summary["project"]["root"],"turn_dir":str(directory)}
            records.append(row)
            (out/(name+".json")).write_text(json.dumps(records,indent=2,ensure_ascii=False).replace(key,"[REDACTED]"),encoding="utf-8")
            print(name,step,row["job"],[c[0] for c in row["calls"]],flush=True)
            if row["job"]!="finished":break
            previous=run_id
    chosen={k:v for k,v in SCENARIOS.items() if not args.case or k in args.case}
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(execute,enumerate(chosen.items(),1)))


if __name__=="__main__":main()
