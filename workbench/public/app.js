const $ = id => document.getElementById(id);
const state = {runId:null, run:null, artifact:null, metric:"exposed_m", geo:null, job:null, newTask:false, grid:null, roads:null, flood:null, satellite:null};
const colors = ["#fff5ad","#f1ca66","#ec9953","#d75d45","#ae2c43","#6a2041"];
const map = L.map("map", {zoomControl:true, preferCanvas:true}).setView([39.395,-.425],12);
L.control.scale({imperial:false, position:"bottomleft"}).addTo(map);
const icons = () => lucide.createIcons();
const number = (v, digits=2) => v == null ? "—" : Number(v).toLocaleString("zh-CN",{minimumFractionDigits:digits,maximumFractionDigits:digits});
const icon = name => {const i=document.createElement("i");i.dataset.lucide=name;return i;};
function toast(text){$("toast").textContent=text;$("toast").classList.remove("hidden");setTimeout(()=>$("toast").classList.add("hidden"),6000);}
async function api(url, body){
  const response=await fetch(url,body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{});
  const result=await response.json();
  if(!response.ok)throw new Error(result.error||`HTTP ${response.status}`);
  return result;
}
const query = args => new URLSearchParams(args).toString();
const markdown=window.markdownit({html:false,linkify:false,typographer:false}).disable(["image","autolink"]);
function renderAnswer(text){
  const body=document.createElement("div");body.className="answer-prose";
  body.innerHTML=markdown.render(text);
  for(const link of body.querySelectorAll("a")){
    const href=link.getAttribute("href")||"";
    const name=href.split(/[\\/]/).pop();
    const artifact=state.run.artifacts.find(a=>[".pdf",".qgz",".png"].some(ext=>a.id+ext===name&&a.files.includes(ext===".qgz"?"path":ext.slice(1))));
    if(artifact){
      const kind=name.endsWith(".qgz")?"path":name.split(".").pop();
      link.href="/api/file?"+query({run:state.runId,artifact:artifact.id,kind,download:"1"});
      link.setAttribute("download",name);
    }else if(/^https?:\/\//i.test(href)){
      link.target="_blank";link.rel="noopener noreferrer";
    }else link.replaceWith(...link.childNodes);
  }
  const fields={total_m:["道路总长","m"],exposed_m:["暴露长度","m"],exposure_pct:["暴露率","%"]};
  for(const table of body.querySelectorAll("table")){
    const wrapper=document.createElement("div");wrapper.className="answer-table";
    wrapper.tabIndex=0;wrapper.setAttribute("role","region");wrapper.setAttribute("aria-label","分析结果表");
    table.before(wrapper);wrapper.append(table);
    for(const cell of table.querySelectorAll("th")){
      cell.scope="col";
      const match=cell.textContent.trim().match(/^(total_m|exposed_m|exposure_pct)\s*[（(]\s*(m|%)\s*[)）]$/);
      if(match&&fields[match[1]][1]===match[2]){
        cell.title=match[1];cell.textContent=fields[match[1]][0];putText(cell,"small"," / "+match[2]);
      }
    }
  }
  return body;
}
function fileURL(kind,download=false){return "/api/file?"+query({run:state.runId,artifact:state.artifact.id,kind,...(download?{download:"1"}:{})});}
function putText(parent,tag,text,className){const e=document.createElement(tag);e.textContent=text;if(className)e.className=className;parent.append(e);return e;}
function outputLabel(o){
  if(o.kind==="satellite_coverage")return "卫星影像覆盖";
  if(o.kind==="karla_evidence")return o.candidate_km2==null?"证据对照":"候选积水 "+number(o.candidate_km2,2)+" km²";
  if(o.kind==="karla_exposure")return "核查地图和清单";
  if(o.kind==="transport_screening")return "道路核查图与清单";
  if(o.kind==="sar_flood_change")return (o.preset==="conservative"?"保守参数":"均衡参数")+" · 候选淹没 "+number(o.total_km2,2)+" km²";
  return number(o.total_km2,3)+" km²";
}
function outputButtons(parent,outputs){
  for(const o of outputs){
    const b=document.createElement("button");b.type="button";b.className="artifact-link";
    b.append(icon("map"),document.createTextNode(o.aoi+" · "+outputLabel(o)));
    b.onclick=()=>showEvent(o);parent.append(b);
  }
}
function renderConversation(){
  updateFolder();
  const box=$("conversation"),follow=box.scrollHeight-box.scrollTop-box.clientHeight<40;
  const oldTop=box.scrollTop;
  box.replaceChildren();
  const turns=state.run?.turns||[];
  document.querySelector(".chat-panel").classList.toggle("empty",!turns.length&&!state.runId);
  if(!turns.length){if(state.runId)putText(box,"p","正在读取任务…","secondary");return;}
  for(const turn of turns){
    const user=document.createElement("div");user.className="message user";
    putText(user,"div","分析要求","role");putText(user,"p",turn.question);box.append(user);
    const answer=document.createElement("div");answer.className="message agent";
    const role=putText(answer,"div","GIS Agent","role agent");role.prepend(icon("sparkles"));
    if(turn.answer){
      answer.append(renderAnswer(turn.answer));
    }else putText(answer,"p",turn.error|| (turn.status==="running"?"正在分析与调用工具…":"本轮未生成完整回答。"));
    let meta=`${turn.calls.length} 次工具调用`;
    if(turn.elapsed_s)meta+=` · ${Math.round(turn.elapsed_s)} 秒${turn.timing_note?"（恢复后）":""}`;
    const record=document.createElement("details");record.className="tool-summary";
    putText(record,"summary",meta);
    for(const call of turn.calls)putText(record,"div",(call.name==="process"?JSON.parse(call.arguments).algorithm:call.name)+" · "+call.response.status);
    if(turn.calls.length)answer.append(record);
    if(turn===turns.at(-1)&&turn.options?.length){
      const choices=document.createElement("div");choices.className="dialogue-options";
      for(const label of turn.options){const b=document.createElement("button");b.type="button";b.textContent=label;b.disabled=!!state.job;b.onclick=()=>{$("question").value=label;$("taskForm").requestSubmit();};choices.append(b);}
      answer.append(choices);
    }
    outputButtons(answer,turn.event_outputs||[]);
    box.append(answer);
  }
  const shown=new Set(turns.flatMap(t=>(t.event_outputs||[]).map(o=>o.folder)));
  outputButtons(box,(state.run.event_outputs||[]).filter(o=>!shown.has(o.folder)));
  if(state.run.artifacts.length){
    const b=document.createElement("button");b.className="artifact-link";b.append(icon("map"),document.createTextNode("查看本次专题图"),icon("arrow-up-right"));b.onclick=()=>setPanel("map");box.append(b);
  }
  icons();
  box.scrollTop=follow?box.scrollHeight:oldTop;
}
function renderTrace(){
  const trace=$("trace");trace.replaceChildren();
  const turn=state.run?.turns.at(-1);
  if(!turn)return;
  const labels={state:"读取任务状态",set_parameters:"更新分析参数",algorithm_help:"查询工具参数",table:"检查统计与排名",export_map:"导出专题图",view_map:"读取地图截图",inspect:"检查图层",
    sar_inventory:"核对雷达观测方案",download_sar_pair:"下载灾前后 Sentinel-1",segment_sar_flood:"提取候选淹没范围",publish_sar_map:"面积分析与专题制图",
    transport_inventory:"核对观测产品与行政区",screen_transport:"统计需核查路段",export_transport_map:"导出道路核查成果"};
  for(const call of turn.calls){
    const row=document.createElement("div");row.className="trace-item"+(call.response.status==="ok"?"":" failed");
    row.append(icon(call.response.status==="ok"?"check":"triangle-alert"));
    const args=JSON.parse(call.arguments);
    const d=document.createElement("details");
    putText(d,"summary",call.name==="process"?args.algorithm.replace("native:",""):labels[call.name]||call.name);
    putText(d,"pre",JSON.stringify({arguments:args,response:call.response},null,2));row.append(d);trace.append(row);
  }
  icons();
}
function breaksFor(){
  if(state.metric==="exposure_pct")return [0,1,5,10,25,50,100];
  const vals=(state.geo?.features||[]).map(f=>Number(f.properties.exposed_m)||0);
  const max=Math.max(...vals,1);
  const step=Math.max(1,Math.ceil(max/5/50)*50);
  return [0,step,step*2,step*3,step*4,step*5];
}
function colorFor(value,b){
  if(value==null)return "#d4deda";
  let i=b.findIndex((v,j)=>j>0&&value<=v)-1;
  if(i<0)i=b.length-2;
  return colors[Math.round(i*(colors.length-1)/(b.length-2))];
}
function displayStats(data){
  return {...data,features:data.features.map(feature=>{
    const p={...feature.properties};
    // The display can derive a percent from measured lengths; downloads stay unchanged.
    if(!Object.hasOwn(p,"exposure_pct")){
      p.exposure_pct=Number.isFinite(p.total_m)&&p.total_m>0&&Number.isFinite(p.exposed_m)
        ?100*p.exposed_m/p.total_m:null;
    }
    return {...feature,properties:p};
  })};
}
function isRateMap(spec){return ["exposure_pct","exposure_rate","exposure_ratio","ratio"].includes(spec.field);}
function drawGrid(){
  if(state.grid){map.removeLayer(state.grid);state.grid=null;}
  const b=breaksFor();
  if(state.geo){
    state.grid=L.geoJSON(state.geo,{style:f=>({color:"#ffffff",weight:.8,fillColor:colorFor(f.properties[state.metric],b),fillOpacity:f.properties[state.metric]==null?.12:.7}),
      onEachFeature:(f,l)=>{
        const p=f.properties,div=document.createElement("div");
        putText(div,"strong",`网格 ${p.id}`);
        putText(div,"div",`道路总长 ${number(p.total_m)} m`);
        putText(div,"div",`暴露长度 ${number(p.exposed_m)} m`);
        putText(div,"div",`暴露率 ${number(p.exposure_pct)} %`);
        l.bindPopup(div);
      }}).addTo(map);
    if(state.flood)state.flood.bringToFront();
  }
  const legend=$("legend");legend.replaceChildren();
  putText(legend,"div",state.metric==="exposure_pct"?"暴露率 / %":"暴露长度 / m","legend-title");
  for(let i=0;i<b.length-1;i++){
    const row=document.createElement("div");row.className="legend-row";
    const swatch=document.createElement("span");swatch.className="swatch";swatch.style.background=colorFor((b[i]+b[i+1])/2,b);row.append(swatch);
    putText(row,"span",`${b[i]} – ${b[i+1]}`);legend.append(row);
  }
  const row=document.createElement("div");row.className="legend-row";
  const swatch=document.createElement("span");swatch.className="swatch";swatch.style.background="#d4deda";row.append(swatch);putText(row,"span","无有效数值");legend.append(row);
  renderStats();
}
function renderStats(){
  const rows=state.geo?.features.map(f=>f.properties)||[];
  const total=rows.reduce((s,p)=>s+(p.total_m||0),0),exposed=rows.reduce((s,p)=>s+(p.exposed_m||0),0);
  $("totalLength").innerHTML=rows.length?`${number(total/1000,2)}<small>km</small>`:"—";
  $("exposedLength").innerHTML=rows.length?`${number(exposed/1000,3)}<small>km</small>`:"—";
  $("overallRatio").innerHTML=total?`${number(exposed/total*100,3)}<small>%</small>`:"—";
  $("gridCount").textContent=rows.length||"—";
  $("rankingUnit").textContent=state.metric==="exposure_pct"?"按暴露率排序":"按暴露长度排序";
  const tbody=$("ranking");tbody.replaceChildren();
  const sorted=rows.filter(p=>p[state.metric]!=null).sort((a,b)=>b[state.metric]-a[state.metric]||a.id-b.id);
  for(const p of sorted.slice(0,8)){
    const row=document.createElement("tr");
    for(const val of [p.id,number(p.exposed_m,1),number(p.exposure_pct,2)])putText(row,"td",val);
    row.tabIndex=0;row.setAttribute("aria-label",`定位网格 ${p.id}`);
    function focus(){const target=state.grid.getLayers().find(l=>l.feature.properties.id===p.id);map.fitBounds(target.getBounds().pad(.4));target.openPopup();setPanel("map");}
    row.onclick=focus;row.onkeydown=e=>{if(e.key==="Enter")focus();};tbody.append(row);
  }
}
async function selectArtifact(id,fit=true){
  state.artifact=state.run.artifacts.find(a=>a.id===id)||null;
  $("mapEmpty").classList.toggle("hidden",!!state.artifact);
  $("versionBadge").classList.toggle("historical",!!state.artifact?.stale);
  $("versionBadge").textContent=state.artifact?.stale?"历史版本":"当前版本";
  function control(id,available,url){
    const el=$(id);el.classList.toggle("disabled",!available);el.setAttribute("aria-disabled",String(!available));
    if(el.tagName==="BUTTON")el.disabled=!available;
    else if(available)el.href=url;else el.removeAttribute("href");
  }
  const artifact=state.artifact,files=artifact?.files||[];
  control("downloadPDF",files.includes("pdf"),artifact&&fileURL("pdf",true));
  control("downloadQGIS",files.includes("path"),artifact&&fileURL("path",true));
  control("openQGIS",files.includes("path"));control("previewMap",files.includes("png"));
  control("downloadData",!!artifact&&artifact.layer_available!==false,artifact&&"/api/data?"+query({run:state.runId,id:artifact.layer_id}));
  $("artifactTitle").textContent=artifact?.spec.title||"尚无专题图";
  if(!artifact||artifact.layer_available===false){
    state.geo=null;drawGrid();$("verification").textContent=artifact?"统计图层文件已移动或丢失，无法重新核验。":"等待分析";
    $("mapEmpty").classList.remove("hidden");$("mapEmpty").textContent=artifact?"统计图层文件不可用":"等待分析图层";return;
  }
  $("artifactTitle").textContent=state.artifact.spec.title;
  state.metric=isRateMap(state.artifact.spec)?"exposure_pct":"exposed_m";
  syncMetricButtons();
  const requestedRun=state.runId,requestedId=state.artifact.id;
  const data=await api("/api/layer?"+query({run:state.runId,id:state.artifact.layer_id}));
  if(state.runId!==requestedRun||state.artifact?.id!==requestedId)return;
  state.geo=displayStats(data);drawGrid();if(fit){state.fitPending=document.querySelector(".workspace").dataset.panel!=="map";if(!state.fitPending&&state.grid.getBounds().isValid())map.fitBounds(state.grid.getBounds().pad(.08));}
  const artifacts=state.run.artifacts,index=artifacts.findIndex(a=>a.id===state.artifact.id);
  const check=state.run.verification?.[index];
  $("verification").textContent=check?.passed?`${check.cells} 个网格已通过独立数值核验；长度绝对允差 0.01 m。`:"本次模型执行已记录；该版本尚无独立数值核验记录。";
}
function syncMetricButtons(){document.querySelectorAll("[data-metric]").forEach(b=>b.classList.toggle("active",b.dataset.metric===state.metric));}
async function loadRun(id,fit=true){
  state.runId=id;state.newTask=false;
  $("threadTitle").textContent=runName(id);paintHistory();
  const run=await api("/api/run?"+query({id}));
  if(state.runId!==id)return;
  state.run=run;renderConversation();renderTrace();
  const select=$("versionSelect"),previous=state.artifact?.id;
  select.replaceChildren();
  run.artifacts.forEach((a,i)=>{const option=document.createElement("option");option.value=a.id;option.textContent=`版本 ${i+1} · ${a.stale?"历史成果":"当前成果"} · ${isRateMap(a.spec)?"暴露率":"暴露长度"}`;select.append(option);});
  const target=(!fit&&run.artifacts.find(a=>a.id===previous))||run.artifacts.at(-1);
  if(target){select.value=target.id;await selectArtifact(target.id,fit);}else await selectArtifact(null);
}
async function refreshStatus(){
  const data=await api("/api/status");
  $("connectionStatus").textContent=data.configured?data.model:"配置模型";
  $("connectionStatus").classList.toggle("ready",data.configured);
  state.demo=data.default_run;state.history=data.runs;state.titles={...state.titles,...data.titles};paintHistory();
  state.job=data.jobs.find(j=>["running","stopping"].includes(j.status))||null;
  $("sendTask").disabled=!!state.job;$("stopTask").classList.toggle("hidden",!state.job);
  $("taskStatus").textContent=state.job?(state.job.status==="stopping"?"等待当前调用结束…":"Agent 正在执行…"):"就绪";
  $("taskStatus").classList.toggle("idle",!state.job);
  return data;
}
function runName(id){return id===state.demo?"瓦伦西亚道路暴露分析":id==="workbench_d0e0a1f802"?"住宅道路 · 500 m 网格":state.titles?.[id]||"分析记录 "+id.slice(-6);}
function paintHistory(){
  const box=$("historyList"), ids=state.history||[];
  const signature=JSON.stringify([ids,state.runId,state.titles]);if(box.dataset.signature===signature)return;
  box.dataset.signature=signature;box.replaceChildren();
  for(const id of ids){const b=document.createElement("button");b.className="history-item";b.textContent=runName(id);b.title=b.textContent;
    b.setAttribute("aria-current",id===state.runId?"true":"false");b.onclick=()=>openThread(id);box.append(b);}
}
async function openThread(id,panel="chat"){
  document.body.classList.remove("drawer");setPanel(panel);try{await loadRun(id);}catch(e){toast(e.message);}
}
function setPanel(panel){
  document.querySelector(".workspace").dataset.panel=panel;
  document.querySelectorAll(".view-nav button").forEach(b=>{b.classList.toggle("active",b.dataset.panel===panel);b.setAttribute("aria-pressed",String(b.dataset.panel===panel));});
  if(panel==="map")requestAnimationFrame(()=>{map.invalidateSize();if(state.grid&&state.geo&&state.grid.getBounds().isValid()&&state.fitPending){map.fitBounds(state.grid.getBounds().pad(.08));state.fitPending=false;}});
}
let coverageMap;
async function showEvent(o){
  const url=(file,download=0)=>"/api/project-output?"+query({run:state.runId,folder:o.folder,file,download});
  if(coverageMap){coverageMap.remove();coverageMap=null;}
  const satellite=o.kind==="satellite_coverage";
  $("eventTitle").textContent=o.aoi+" · "+outputLabel(o);
  $("eventPreview").classList.toggle("hidden",satellite);
  $("satelliteCoverage").classList.toggle("hidden",!satellite);
  if(!satellite)$("eventPreview").src=url(o.preview_file||"map.png");
  const files=satellite?[["coverage.geojson","影像足迹 GeoJSON"],["results.json","查询范围与元数据"]]:
    o.kind==="karla_evidence"?[["map.pdf","专题图 PDF"],[o.candidate_km2==null?"agreement.tif":"evidence.tif","证据分类 GeoTIFF"],
    ["results.json","分析结果与口径"],["project.zip","完整 QGIS 工程包"]]:
    o.kind==="karla_exposure"?[["map.pdf","核查图 PDF"],["cropland.csv","农田核查 CSV"],["roads.csv","路段清单 CSV"],
    ["project.zip","完整 QGIS 工程包"]]:
    o.kind==="sar_flood_change"?[["map.pdf","专题图 PDF"],["candidate.tif","分割掩膜 GeoTIFF"],
    ["candidate.gpkg","淹没候选 GeoPackage"],["agreement.tif","异时相对照栅格"],["results.json","分区统计与对照"],["project.zip","完整 QGIS 工程包"]]:
    o.kind==="transport_screening"?[["transport.pdf","核查图 PDF"],["segments.csv","候选路段 CSV"],
    ["municipalities.csv","市镇汇总 CSV"],["corridors.csv","道路汇总 CSV"],["project.zip","完整 QGIS 工程包"]]:
    [["map.pdf","专题图 PDF"],["grid.geojson","网格 GeoJSON"],["results.json","面积与排名"],["map.qgz","QGIS 工程"]];
  $("eventFiles").replaceChildren();for(const [file,text] of files){
    const a=document.createElement("a");a.href=url(file,1);a.textContent=text;$("eventFiles").append(a);}
  $("eventDialog").showModal();
  if(!satellite)return;
  const [w,s,e,n]=o.bbox,bounds=[[s,w],[n,e]];
  const target=L.map("satelliteFootprints",{attributionControl:false,scrollWheelZoom:false,maxZoom:16});
  coverageMap=target;target.fitBounds(bounds,{padding:[32,32]});
  const windowLayer=L.rectangle(bounds,{color:"#222c34",weight:2,dashArray:"6 4",fill:false}).addTo(target);
  L.control.scale({imperial:false}).addTo(target);
  $("satelliteSummary").textContent=o.from+" 至 "+o.to+" · 黑色虚线：检索框；紫色：Sentinel-1；蓝色：Sentinel-2。仅为影像足迹，不是洪水范围。";
  try{
    const features=await api(url("coverage.geojson"));
    if(target!==coverageMap||!$("eventDialog").open)return;
    L.geoJSON(features,{
      style:f=>({color:f.properties.collection.startsWith("sentinel-1")?"#583e92":"#1b71a5",weight:2,fillOpacity:.12}),
      onEachFeature:(f,l)=>{const p=f.properties,content=document.createElement("div");putText(content,"strong",p.collection);putText(content,"p",p.datetime_local);if(p.cloud_pct_scene!=null)putText(content,"p","整景云量："+p.cloud_pct_scene+"%");if(p.orbit!=null)putText(content,"p","相对轨道："+p.orbit);l.bindPopup(content);}
    }).addTo(target);
    windowLayer.bringToFront();
  }catch(error){toast("无法读取影像足迹："+error.message);}
}
$("eventDialog").addEventListener("close",()=>{if(coverageMap){coverageMap.remove();coverageMap=null;}});
function updateFolder(){
  const info=state.runId?state.run?.project:state.folder;
  $("projectFolder").querySelector("span").textContent=info?.root?info.root.split(/[\\/]/).at(-1):info?.name||"默认工作区";
  $("projectFolder").title=info?.root||info?.path||"选择对话工作区";
}
async function chooseFolder(path){if(!path)return;state.folder=await api("/api/folder",{parent:path});updateFolder();}
$("projectFolder").onclick=async()=>{try{
  if(state.runId){const root=state.run?.project?.root;if(!root){toast("这是历史案例；新建对话时可以选择工作区");return;}
    if(window.floodlab)await window.floodlab.openFolder(state.runId);else toast(root);return;}
  if(window.floodlab){await chooseFolder(await window.floodlab.chooseFolder());return;}
  $("folderError").textContent="";$("folderDialog").showModal();
}catch(e){toast(e.message);}};
$("folderForm").onsubmit=async e=>{e.preventDefault();try{await chooseFolder($("folderPath").value.trim());$("folderDialog").close();}catch(err){$("folderError").textContent=err.message;}};
document.querySelectorAll(".view-nav button").forEach(b=>b.onclick=()=>{if(b.dataset.panel!=="chat"&&state.runId&&state.run?.event_outputs?.length){showEvent(state.run.event_outputs.at(-1));return;}if(b.dataset.panel!=="chat"&&state.runId&&!state.run?.artifacts.length){toast("这次对话尚未生成地图或空间分析成果");return;}if(!state.runId&&b.dataset.panel!=="chat"&&state.demo)openThread(state.demo,b.dataset.panel);else setPanel(b.dataset.panel);});
$("toggleSidebar").onclick=()=>{document.body.classList.toggle(matchMedia("(max-width:760px)").matches?"drawer":"collapsed");};
$("scrim").onclick=()=>document.body.classList.remove("drawer");
document.addEventListener("keydown",e=>{if(e.key==="Escape")document.body.classList.remove("drawer");});
$("example").onclick=()=>{if(state.demo)openThread(state.demo);};
document.querySelectorAll("[data-prompt]").forEach(b=>b.onclick=()=>{$("question").value=b.dataset.prompt;$("question").focus();});
$("question").onkeydown=e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();if(!$("sendTask").disabled)$("taskForm").requestSubmit();}};
document.querySelectorAll("[data-metric]").forEach(b=>b.onclick=()=>{state.metric=b.dataset.metric;syncMetricButtons();drawGrid();});
$("versionSelect").onchange=e=>selectArtifact(e.target.value).catch(e=>toast(e.message));
$("fitMap").onclick=()=>state.grid?map.fitBounds(state.grid.getBounds().pad(.08)):map.fitBounds([[39.34,-.49],[39.45,-.36]]);
$("previewMap").onclick=()=>{if(state.artifact){$("mapPreview").src=fileURL("png");$("mapDialog").showModal();}};
document.querySelectorAll(".close-dialog").forEach(b=>b.onclick=()=>b.closest("dialog").close());
$("connectionButton").onclick=async()=>{
  const status=await api("/api/status");
  $("baseURL").value=status.base_url;$("modelName").value=status.model;
  $("connectionError").textContent="";$("connectionDialog").showModal();
};
$("connectionForm").onsubmit=async e=>{
  e.preventDefault();$("connectionError").textContent="连接中…";
  const config={key:$("apiKey").value,base_url:$("baseURL").value,model:$("modelName").value};
  try{
    await api("/api/connection",config);
    await refreshStatus();
    if(window.floodlab)await window.floodlab.saveConnection({...config,remember:$("rememberKey").checked});
    $("apiKey").value="";$("connectionDialog").close();await refreshStatus();
  }catch(error){$("connectionError").textContent=error.message;}
};
$("disconnectButton").onclick=async()=>{
  try{
    await api("/api/disconnect",{});
    if(window.floodlab)await window.floodlab.forgetConnection();
    $("apiKey").value="";$("rememberKey").checked=false;
    $("connectionError").textContent="已清除连接";await refreshStatus();
  }catch(error){$("connectionError").textContent=error.message;}
};
if(window.floodlab){
  $("rememberRow").classList.remove("hidden");
  $("openQGIS").classList.remove("hidden");
  $("openQGIS").onclick=async()=>{
    try{
      const href=$("downloadQGIS").getAttribute("href");
      if(!href)throw new Error("请先选择一个制图成果");
      const params=new URL(href,location.origin).searchParams;
      await window.floodlab.openProject({run:params.get("run"),artifact:params.get("artifact")});
    }catch(error){$("taskStatus").textContent=error.message;}
  };
}
$("newTask").onclick=()=>{state.folder=null;state.newTask=true;state.runId=null;state.run=null;state.artifact=null;state.geo=null;$("question").value="";$("threadTitle").textContent="新对话";paintHistory();document.body.classList.remove("drawer");$("versionSelect").replaceChildren();renderConversation();renderTrace();drawGrid();$("artifactTitle").textContent="尚无专题图";$("verification").textContent="等待分析";$("mapEmpty").classList.remove("hidden");document.querySelectorAll(".downloads a,.downloads button").forEach(e=>e.classList.add("disabled"));setPanel("chat");$("question").focus();};
$("taskForm").onsubmit=async e=>{
  e.preventDefault();const question=$("question").value.trim();if(!question)return;
  $("sendTask").disabled=true;
  try{
    if(!$("connectionStatus").classList.contains("ready")){await $("connectionButton").onclick();$("sendTask").disabled=false;return;}
    const result=await api("/api/task",{question,run_id:state.newTask?null:state.runId,folder_key:state.runId?undefined:state.folder?.key,utc_offset_minutes:-new Date().getTimezoneOffset()});
    state.pendingJob=result.job_id;state.runId=result.run_id;state.titles??={};state.titles[result.run_id]=question.slice(0,28);
    state.newTask=false;$("question").value="";$("threadTitle").textContent=runName(result.run_id);
    await loadRun(result.run_id,false);await pollJobs();
  }
  catch(error){toast(error.message);$("sendTask").disabled=false;}
};
$("stopTask").onclick=async()=>{try{if(state.job){await api("/api/stop",{id:state.job.id});await pollJobs();}}catch(e){toast(e.message);}};
let polling=false;
async function pollJobs(){
  if(polling)return;polling=true;
  try{
    const latest=await refreshStatus();
    const job=latest.jobs.find(j=>j.id===state.pendingJob)||state.job;
    if(!job)return;
    state.pendingJob=job.id;
    if(job.run_id===state.runId)await loadRun(job.run_id,false);
    if(!["running","stopping"].includes(job.status)){
      state.pendingJob=null;
      const messages={finished:"本轮执行结束",cancelled:"任务已停止",incomplete:"本轮未完成，请查看执行记录",failed:"本轮失败："+(job.error||"请查看执行记录")};
      toast(messages[job.status]||"请查看执行记录");
    }
  }finally{polling=false;}
}
async function init(){
  icons();
  const data=await refreshStatus();
  const image=await api("/api/image");state.satellite=L.imageOverlay("/satellite.png",image.bounds,{opacity:.88,attribution:"Sentinel-2 / Copernicus"}).addTo(map);
  const [aoi,flood]=await Promise.all([api("/api/context?id=aoi"),api("/api/context?id=flood")]);
  L.geoJSON(aoi,{style:{color:"#243e34",weight:2,fillOpacity:0,dashArray:"5 4"}}).addTo(map);
  state.flood=L.geoJSON(flood,{style:{color:"#137fbe",weight:1.2,fillColor:"#198dca",fillOpacity:.12},attribution:"Copernicus EMSR773"}).addTo(map);
  $("showSatellite").onchange=e=>e.target.checked?state.satellite.addTo(map):map.removeLayer(state.satellite);
  $("showFlood").onchange=e=>e.target.checked?state.flood.addTo(map):map.removeLayer(state.flood);
  $("showRoads").onchange=async e=>{try{if(e.target.checked){if(!state.roads)state.roads=L.geoJSON(await api("/api/context?id=roads"),{style:{color:"#425c51",weight:1,opacity:.65},attribution:"© OpenStreetMap contributors / ODbL"});state.roads.addTo(map);}else if(state.roads)map.removeLayer(state.roads);}catch(error){toast(error.message);}};
  if(state.job)await loadRun(state.job.run_id);else if(!state.runId)renderConversation();
  if(state.job)state.pendingJob=state.job.id;
  setInterval(()=>pollJobs().catch(e=>console.warn(e.message)),2500);
}
init().catch(e=>toast(e.message));
