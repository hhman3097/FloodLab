"""Domestic dispatch search and bounded, original-page evidence extraction."""
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

REGIONS="北京 天津 上海 重庆 河北 山西 辽宁 吉林 黑龙江 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 广西 海南 四川 贵州 云南 陕西 甘肃 青海 台湾 内蒙古 宁夏 新疆 西藏 香港 澳门".split()
HAZARD=re.compile("洪涝|洪灾|山洪|内涝|超警|洪峰|洪水|积水|受淹|被淹|淹没|漫堤")
OBSERVED=re.compile("发生|暴发|突发|超警|受淹|被淹|淹没|漫堤|受灾|被困|倒灌|转移|救援")
BACKGROUND=re.compile(r"预警|预计|或将|风险|演练|规划|重建|重构|论坛|科研|赛事|培训|公示|科普|展望|评比|新村民|到20\d\d年|研究成果")
OBSERVED_DETAIL=re.compile(r"(?:发生|出现|遭遇|突发|暴发).{0,9}(?:超警洪水|山洪|洪涝|内涝)|(?:河流|河道|水位).{0,10}超警|(?:路段|道路|房屋|农田|城区).{0,10}(?:受淹|被淹|倒灌)")


def clean(value):
    if isinstance(value,list):value="".join(map(str,value))
    return " ".join(html.unescape(re.sub("<[^<>]+>","",str(value or ""))).split())


def terms(query):
    regions=[r for r in REGIONS if r in query]
    region=regions[0] if len(regions)==1 else ""
    short=query.strip() if len(query.strip())<=20 else ""
    keys=[short,*(f"{region} {k}".strip() for k in ("洪涝灾害","超警洪水","内涝","受淹"))]
    return list(dict.fromkeys(k for k in keys if k)),region


def cn_candidates(term,start,today,get):
    page=get("https://sou.chinanews.com.cn/search.do?"+urlencode({"q":term})).decode("utf-8")
    marker=re.search(r"\bvar\s+docArr\s*=\s*",page)
    if not marker:raise ValueError("search page structure changed")
    entries=json.JSONDecoder().raw_decode(page,marker.end())[0]
    rows=[]
    for item in entries or []:
        url=urlsplit(str(item.get("url","")))
        day=str(item.get("pubtime",""))[:10]
        if not str(start)<=day<=str(today) or url.hostname not in ("www.chinanews.com.cn","www.chinanews.com"):continue
        if not re.fullmatch(r"/[\w-]+/\d{4}/\d{2}-\d{2}/\d+\.shtml",url.path) or url.path.startswith("/gj/"):continue
        title=clean(item.get("title"));snippet=clean(item.get("content_without_tag"))
        rows.append({"date":day,"title":title,"snippet":snippet[:650],"publisher":"中国新闻网",
                     "kind":"站内搜索摘要（待核实）","url":"https://"+url.hostname+url.path})
    return rows


class Article(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.paragraph=None;self.paragraphs=[]
    def handle_starttag(self,tag,attrs):
        if tag=="div":
            if self.depth:self.depth+=1
            elif "left_zw" in dict(attrs).get("class","").split():self.depth=1
        if self.depth and tag=="p":self.paragraph=[]
    def handle_endtag(self,tag):
        if tag=="p" and self.paragraph is not None:
            self.paragraphs.append("".join(self.paragraph).strip());self.paragraph=None
        if tag=="div" and self.depth:self.depth-=1
    def handle_data(self,text):
        if self.paragraph is not None:self.paragraph.append(text)


def rank(row,region=""):
    title=row["title"];body=row.get("excerpt") or row.get("snippet","")
    if row.get("publisher")=="GDACS":return 9
    if not HAZARD.search(title+" "+body):return -8
    if region and region not in title+body:return -6
    score=(3*bool(HAZARD.search(title))+2*bool(OBSERVED.search(title))+bool(OBSERVED.search(body))
           +3*bool(OBSERVED_DETAIL.search(body))+bool(row.get("excerpt"))+bool(body)-6*bool(BACKGROUND.search(title)))
    if re.search("预计|可能发生|将出现|据预报|评比现场|据泰国|总领馆提醒",title+" "+body[:200]):score-=4
    return score


def read_original(row,get):
    parser=Article();parser.feed(get(row["url"]).decode("utf-8",errors="replace"))
    paragraphs=[p for p in parser.paragraphs if p]
    if not paragraphs:return row
    # Keep the lead and surrounding event dates; do not lift single numbers out of context.
    evidence=list(dict.fromkeys([paragraphs[0]]+[p for p in paragraphs if HAZARD.search(p)]))
    return {**row,"kind":"原文段落（发布日期不等于发生日期）","excerpt":"\n".join(evidence)[:1200]}
