"""Live end-to-end conversational checks, isolated from the user's chat history."""
import getpass
import argparse
import json
import os
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"workbench"))
import server
from dialogue import decide


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--fixture-only",action="store_true")
    parser.add_argument("--repeat",type=int,default=1)
    args=parser.parse_args()
    out=Path(os.getenv("FLOODLAB_QA_OUT",ROOT/"desktop/qa"))
    api=server.API("https://api.clawlab.chat/v1",os.getenv("AI4S_API_KEY") or getpass.getpass("Test key: "),"gpt-6.2-sol")
    if not args.fixture_only:live(out,api)
    for i in range(args.repeat):controlled(out,api,i)


def live(out,api):
    results=[]
    with TemporaryDirectory() as tmp:
        server.RUNS=Path(tmp)
        directory=Path(tmp)/"workbench_test"
        directory.mkdir()
        (directory/"dialogue.json").write_text('{"turns":[],"utc_offset_minutes":-300}')
        questions=["最近国内哪里发生洪涝了吗？","那我们分析下吧。","第一个吧。","我不想看国内了。换成瓦伦西亚的示例，先不要做图，告诉我可以怎么分析。","先用瓦伦西亚样例实际查一下 roads 有多少条、什么坐标系。不要制图。"]
        for index,q in enumerate(questions):
            server.JOBS["test"]={"status":"running"}
            server.worker("test",api,directory,q,index>0,threading.Event())
            summary=server.summary(directory.name)
            turn=summary["turns"][-1]
            row={"question":q,"answer":turn.get("answer"),"options":turn.get("options"),"scope":summary["scope"],
                 "calls":[c["name"] for c in turn["calls"]],"job":server.JOBS["test"]["status"]}
            results.append(row)
            (out/"dialogue-live.json").write_text(json.dumps(results,indent=2,ensure_ascii=False))
            print(index+1,row["job"],row["scope"],row["calls"],flush=True)
            assert row["job"]=="finished",row["answer"]
            if index==0:assert "search_floods" in row["calls"]
            if index==1:assert row["options"] and "process" not in row["calls"]
            if index==3:assert not (directory/"worker-checkpoint.json").exists()
            if index==4:assert "inspect" in row["calls"] and (directory/"worker-checkpoint.json").exists()

def controlled(out,api,index):
    # Ask about the fixture as a simulation; never ask the model to attest it as a real disaster.
    sources=[{"publisher":"测试资料","url":f"https://example.org/{i}","kind":"测试报道标题","date":"2026-11-20","title":title}
             for i,title in enumerate(["甲县通报昨日洪水漫入城区","乙县通报昨日洪水淹没部分道路","丙县开展防汛演练，未发生洪水"])]
    def fixture(query,iso,days,today):
        return {"query":query,"from":"2026-11-01","to":str(today),"errors":[],"sources":sources}
    memory={};history=[];checks=[]
    first=("最近国内有哪里发生洪水了？" if index%2==0 else
           "这是检索演习。请搜索模拟资料，分别列出近期洪水线索里的地点，让我选一个。不要把测试标题当作已证实的真实灾情。")
    for q in [first,"只看乙县，分析下吧。","就做事件简报，不用画图。"]:
        d=decide(api,q,history,memory,False,threading.Event(),fixture)
        checks.append({"question":q,"answer":d["answer"],"options":d["options"],"searched":d["searched"]})
        history.append(checks[-1]);memory=d
        assert not d["task"]
    (out/f"dialogue-fixture-{index+1}.json").write_text(json.dumps(checks,ensure_ascii=False,indent=2))
    assert checks[0]["searched"] and "甲县" in checks[0]["answer"] and "乙县" in checks[0]["answer"]
    assert "乙县" in checks[1]["answer"] and checks[1]["options"]
    assert "乙县" in checks[2]["answer"]

if __name__=="__main__":main()
