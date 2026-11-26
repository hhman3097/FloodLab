import json
from http.client import RemoteDisconnected
from urllib.error import URLError
import ssl

import pytest

import gis_agent
import responses_api


def test_tool_cap_keeps_partial_summary_and_disables_tools(monkeypatch,tmp_path):
    class GIS:
        ready={}
        def __init__(self,*args,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*exc):pass
        def call(self,*args,**kwargs):return {"status":"ok","result":{"value":23}}

    class Model:
        base="fixture"
        model="fixture"
        calls=[]
        def complete(self,messages,*,allow_tools=True):
            self.calls.append(allow_tools)
            if allow_tools:
                message={"role":"assistant","tool_calls":[{"id":"1","function":{"name":"table","arguments":"{}"}}]}
            else:
                assert "23" in messages[-2]["content"]
                message={"role":"assistant","content":"取得 23；尚未生成地图。"}
            return {"choices":[{"message":message}]}

    monkeypatch.setattr(gis_agent,"GISClient",GIS)
    api=Model()
    gis_agent.run(api,tmp_path,["制图"],max_calls=1,summarize_at_limit=True)
    turn=json.loads((tmp_path/"agent-run.json").read_text())["turns"][0]
    assert turn["status"]=="tool_budget_exhausted"
    assert turn["answer"]=="取得 23；尚未生成地图。"
    assert api.calls==[True,False] and len(turn["calls"])==1


@pytest.mark.parametrize("failure",[RemoteDisconnected("fixture"),URLError(ssl.SSLEOFError("fixture"))])
def test_transient_response_retry_is_bounded_and_reuses_request(monkeypatch,failure):
    api=responses_api.API("https://example.test","fixture","fixture")
    calls=[]
    def request(path,body):
        calls.append(body)
        if len(calls)==1:raise failure
        return {"output":[{"type":"message","content":[{"text":"done"}]}]}
    monkeypatch.setattr(api,"request",request)
    monkeypatch.setattr(responses_api.time,"sleep",lambda _:None)
    assert api.complete([{"role":"user","content":"test"}],allow_tools=False)["choices"][0]["message"]["content"]=="done"
    assert len(calls)==2 and calls[0] is calls[1] and not calls[1]["tools"]
