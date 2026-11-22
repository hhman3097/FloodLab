"""Run source acceptance in a private sandbox, keep individual logs and failures."""
import argparse
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"workbench"))
import server


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--out",type=Path,required=True)
    parser.add_argument("--live",action="store_true",help="Use billed provider and online event sources")
    parser.add_argument("--only",action="append",choices=["pytest","browser","browser-recovery","live-project","live-followups","live-discovery"])
    args=parser.parse_args()
    if any(n.startswith("live-") for n in args.only or []) and not args.live:
        parser.error("Live cases require --live")
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    key=(os.environ.get("AI4S_API_KEY") or getpass.getpass("Test key (not stored): ")) if args.live else ""
    server.RUNS=out/"runs";server.RUNS.mkdir()
    server.CACHE=out/"cache";server.prepare_image()
    for sample in (server.DEMO,"workbench_d0e0a1f802"):
        shutil.copytree(ROOT/"event/runs"/sample,server.RUNS/sample)
    http=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    env={**os.environ,"FLOODLAB_PREVIEW":f"http://127.0.0.1:{http.server_port}","FLOODLAB_QA_OUT":str(out)}
    env.pop("AI4S_API_KEY",None)
    runs=[]
    def check(name,cmd,live=False):
        if args.only and name not in args.only:return
        start=time.monotonic()
        try:
            process=subprocess.run(cmd,cwd=ROOT,env={**env,**({"AI4S_API_KEY":key} if live else {})},
                                   capture_output=True,text=True,timeout=650)
            log=process.stdout+process.stderr;code=process.returncode
        except (OSError,subprocess.TimeoutExpired) as error:log=str(error);code=-1
        if key:log=log.replace(key,"[REDACTED]")
        (out/(name+".log")).write_text(log,encoding="utf-8")
        runs.append({"name":name,"exit":code,"seconds":round(time.monotonic()-start,1)})
        print(name,code,runs[-1]["seconds"],flush=True)
        (out/"acceptance.json").write_text(json.dumps(runs,indent=2))
    try:
        check("pytest",[sys.executable,"-m","pytest","-q","--junitxml",str(out/"pytest.xml")])
        check("browser",["node","workbench/qa.mjs"])
        check("browser-recovery",["node","desktop/check_recovery_ui.mjs"])
        if args.live:
            check("live-project",[sys.executable,"desktop/check_projects.py"],True)
            check("live-followups",[sys.executable,"desktop/check_followups.py","after"],True)
            check("live-discovery",[sys.executable,"desktop/check_dialogue.py"],True)
    finally:
        server.KEY="";http.shutdown();http.server_close();thread.join()
    return int(any(r["exit"] for r in runs))

if __name__=="__main__":sys.exit(main())
