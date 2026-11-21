import json
from datetime import date
from flood_search import cn_candidates, read_original, terms, rank


def test_split_scope_and_candidate_validation():
    assert terms("广东 广西 海南 福建 浙江 最近哪里发生洪水")[1]==""
    assert "广东 内涝" in terms("广东最近洪涝")[0]
    assert "广东" not in terms("广西 内涝")[0]
    good={"title":["海南<em>受淹</em>"],"pubtime":"2026-09-14 13:00:00",
          "url":"http://www.chinanews.com.cn/sh/2026/09-14/123.shtml","content_without_tag":"房屋受淹"}
    rows=[good,{**good,"url":"https://other.invalid/report"},{**good,"pubtime":"2026-09-01"}]
    get=lambda url:("var docArr = "+json.dumps(rows)+";").encode()
    hit,=cn_candidates("受淹",date(2026,9,10),date(2026,9,14),get)
    assert hit["title"]=="海南受淹" and hit["url"].startswith("https://")


def test_article_boundary_and_ranking():
    response='<p>其他地方受淹</p><div class="left_zw"><p>当地14日电</p><div><p>当日城区道路受淹。</p></div></div><p>旁栏洪水</p>'
    row={"title":"海南城区受淹","url":"unused"}
    hit=read_original(row,lambda url:response.encode())
    assert hit["excerpt"]=="当地14日电\n当日城区道路受淹。"
    assert rank(hit)>rank({**hit,"title":"灾后重建规划"})
