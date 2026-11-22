"""Exercise history, lifecycle and HTTP continuation without paying for inference."""
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import server


@pytest.fixture
def env(tmp_path, monkeypatch):
    runs=tmp_path/"runs";runs.mkdir()
    monkeypatch.setattr(server,"RUNS",runs);monkeypatch.setattr(server,"JOBS",{})
    monkeypatch.setattr(server,"KEY","test-local-key")
    monkeypatch.delenv("AI4S_DESKTOP_TOKEN",raising=False)
    service=server.ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    def request(route,body=None):
        req=Request(f"http://127.0.0.1:{service.server_port}"+route,
                    None if body is None else json.dumps(body).encode(),{"Content-Type":"application/json"})
        try:r=urlopen(req,timeout=5)
        except HTTPError as exc:r=exc
        with r:return r.status,json.load(r)
    yield runs,request
    service.shutdown();service.server_close();thread.join()


def write(path,obj):
    path.write_text(json.dumps(obj),encoding="utf-8")


def test_missing_layer_does_not_hide_history(env):
    runs,get=env
    directory=runs/"demo";directory.mkdir()
    write(directory/"agent-run.json",{"turns":[]})
    write(directory/"dialogue.json",{"turns":[{"before_gis":0,"question":"查看结果","answer":"已完成"}]})
    write(directory/"state.json",{"layers":{"l":{"path":str(directory/"moved.gpkg")}},
        "artifacts":{"map":{"inputs":["l"],"spec":{},"pdf":"missing.pdf","png":"missing.png","path":"missing.qgz"}}})
    code,result=get("/api/run?id=demo")
    assert code==200 and result["turns"][0]["answer"]=="已完成"
    assert result["verification"]==[None] and result["artifacts"][0]["layer_available"] is False


@pytest.mark.parametrize("terminal",["finished","cancelled","failed","incomplete"])
def test_late_stop_does_not_wedge_queue(env,terminal):
    _,post=env
    event=threading.Event();server.JOBS["j"]={"id":"j","status":terminal,"event":event}
    code,data=post("/api/stop",{"id":"j"})
    assert code==200 and data["status"]==terminal and not event.is_set()


def test_followup_after_provider_error_preserves_transcript_without_replay(env,monkeypatch):
    runs,post=env
    origin=runs/"workbench_abcd000000";origin.mkdir()
    write(origin/"agent-run.json",{"status":"provider_error","turns":[
        {"question":"旧问题","answer":"旧答案","status":"model_finished_unverified","calls":[]},
        {"question":"中断问题","status":"provider_error","error":"上游失败","calls":[]}]})
    write(origin/"state.json",{"layers":{},"artifacts":{}})
    seen=threading.Event()
    def decide(*args,**kwargs):
        assert [t["question"] for t in args[2]]==["旧问题","中断问题"]
        seen.set()
        return {"scope":"external","evidence":{},"answer":"可以继续","options":[],"searched":False,"maps":[],"task":None}
    monkeypatch.setattr(server,"decide",decide)
    code,data=post("/api/task",{"run_id":origin.name,"question":"只解释上轮发生了什么"})
    assert code==200 and seen.wait(4)
    directory=server.run_path(data["run_id"])
    assert not (directory/"agent-run.json").exists() and not (directory/"state.json").exists()
    assert json.loads((origin/"agent-run.json").read_text())["status"]=="provider_error"


def test_budget_exhaustion_is_not_success(env,monkeypatch):
    runs,_=env
    directory=runs/"budget";directory.mkdir();write(directory/"dialogue.json",{"turns":[]})
    monkeypatch.setattr(server,"decide",lambda *a,**kw:dict(scope="local",evidence={},answer="",options=[],searched=False,task="制图"))
    monkeypatch.setattr(server,"run",lambda *a,**kw:write(directory/"agent-run.json",{
        "status":"execution_finished_not_independently_evaluated","turns":[{"status":"tool_budget_exhausted"}]}))
    server.JOBS["j"]={}
    server.worker("j",SimpleNamespace(model="fixture",key="testkey"),directory,"制图",False,threading.Event())
    assert server.JOBS["j"]["status"]=="incomplete"
    assert json.loads((directory/"dialogue.json").read_text())["status"]=="incomplete"


def test_custom_workspace_qgis_uses_registered_folder_only(env):
    runs,get=env
    p=server.Projects(runs)
    directory,info=p.bind("workbench_eeeeeeeeee",None,p.bookmark(runs.parent)["key"])
    write(directory/"dialogue.json",{"turns":[]})
    file=directory/"analysis.qgz";file.touch()
    write(directory/"state.json",{"artifacts":{"map":{"path":str(file)}}})
    assert server.project_file(directory.name,file)==file.resolve()
    outside=runs.parent/"not-in-workspace.qgz";outside.touch()
    with pytest.raises(ValueError):server.project_file(directory.name,outside)
