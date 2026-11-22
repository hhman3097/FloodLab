import threading
import pytest
from types import SimpleNamespace

import pi_bridge
from dialogue import decide

@pytest.mark.parametrize("catalog",[False,True])
def test_unbound_news_reference_without_news_is_not_a_provider_failure(tmp_path,monkeypatch,catalog):
    product={"key":"EMSR933:2962","aoi":"Kastelli","observations":["2026-10-06T10:47:00"],"catalog":"https://example.org/ems"}
    audit=[{"name":"list_products","response":{"status":"ok","result":[product]}}] if catalog else []
    project=SimpleNamespace(root=tmp_path,index={"products":{},"outputs":[]},audit=audit)
    monkeypatch.setattr(pi_bridge,"turn",lambda *a:{"scope":"external","action":"reply","answer":"未知断言 [S1]","refs":[0]})
    result=decide(None,"查 Kastelli 观测日期",[],{},False,threading.Event(),project=project)
    assert "[S1]" not in result["answer"] and "未知断言" not in result["answer"]
    assert ("2026-10-06" in result["answer"])==catalog


def test_only_requested_existing_project_version(tmp_path,monkeypatch):
    root=tmp_path/"project";root.mkdir()
    products=[{"folder":folder,"key":"same-product","grid_m":grid} for folder,grid in
              [("old",500),("new",1000),("missing",2000),("../elsewhere",5000)]]
    for name in ("old","new","../elsewhere"):
        dest=root/name;dest.mkdir();(dest/"map.png").touch()
    project=SimpleNamespace(root=root,index={"products":{},"outputs":products},audit=[])
    def replay(api,policy,context,cancel,tool):
        assert [(r["ref"],r["grid_m"]) for r in context["project"]["maps"]]==[("M1",500),("M2",1000)]
        return {"scope":"external","action":"reply","map_refs":["M1","M1","M3","../elsewhere"]}
    monkeypatch.setattr(pi_bridge,"turn",replay)
    d=decide(None,"看500米的旧版",[],{},False,threading.Event(),project=project)
    assert d["maps"]==products[:1]


def test_empty_news_does_not_erase_successful_spatial_result(tmp_path,monkeypatch):
    project=SimpleNamespace(root=tmp_path,index={"products":{},"outputs":[]},audit=[])
    def call(name,args):
        result={"total_km2":1.25}
        project.audit.append({"name":name,"response":{"status":"ok","result":result}})
        return result
    project.call=call
    def replay(api,policy,context,cancel,tool):
        tool({"iso":"GRC","query":"案例","days":1},"search_floods")
        result=tool({"key":"example"},"analyse_footprint")
        return {"scope":"external","action":"reply","answer":f"观测覆盖 {result['total_km2']} 平方公里。"}
    monkeypatch.setattr(pi_bridge,"turn",replay)
    def empty(q,iso,days,today):return {"query":q,"sources":[],"errors":[],"from":str(today),"to":str(today)}
    d=decide(None,"新闻和已有产品各说明什么？",[],{},False,threading.Event(),search=empty,project=project)
    assert "1.25" in d["answer"] and "新闻检索未返回" in d["answer"]
