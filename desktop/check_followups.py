"""Exercise real Pi follow-ups on an isolated copy of the published Kastelli case."""
import argparse
import getpass
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"workbench"))
import server

CASES=[
    ("denominator","刚才第一名的 7.2% 是整个克里特岛有 7.2% 被淹了吗？只解释分母，不要重算。",True),
    ("change_place","换到海南保亭。先别联网、别下载、别计算，只说如果想做道路分析还缺哪些资料。",False),
    ("return","还是回到刚才 Kastelli 那张已有的图，请让我再看一眼，不要重新下载或计算。",True),
]

def main():
    parser=argparse.ArgumentParser();parser.add_argument("label",choices=["before","after"])
    label=parser.parse_args().label
    api=server.API("https://api.clawlab.chat/v1",os.getenv("AI4S_API_KEY") or getpass.getpass("Test key: "),"gpt-6.2-sol")
    fixture=ROOT/"desktop/qa/用户工作区/FloodLab-a895f4b124"
    report=[]
    with tempfile.TemporaryDirectory() as temp:
        server.RUNS=Path(temp)/"runs";server.RUNS.mkdir()
        runs=server.Projects(server.RUNS)
        previous=None
        for i,(case,question,want) in enumerate(CASES):
            run=f"workbench_{i+1:010x}"
            dest,info=runs.bind(run,previous,None)
            if previous is None:
                for name in ("data-index.json","inputs","outputs"):
                    p=fixture/name;q=Path(info["root"])/name
                    if p.is_dir():shutil.copytree(p,q)
                    else:shutil.copy2(p,q)
                prev=fixture/"turns/workbench_67775251d0"
            else:prev=runs.directory(previous)
            shutil.copy2(prev/"dialogue.json",dest/"dialogue.json")
            server.JOBS[run]={}
            server.worker(run,api,dest,question,True,threading.Event())
            summary=server.summary(run);last=summary["turns"][-1]
            files=[v["key"] for v in summary["event_outputs"]]
            calls=[c["name"] for c in last["calls"]]
            report.append({"case":case,"question":question,"status":last["status"],"answer":last.get("answer"),
                         "calls":calls,"displayed_products":files,"expected_map":want,
                         "pass":last["status"]=="finished" and not calls and files==(["EMSR933:2962"] if want else [])})
            print(case,report[-1]["pass"],calls,files,flush=True)
            previous=run
    path=Path(os.getenv("FLOODLAB_QA_OUT",ROOT/"desktop/qa"))/f"followups-{label}.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    if not all(r["pass"] for r in report):sys.exit(1)

if __name__=="__main__":main()
