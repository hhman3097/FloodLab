import {Agent} from "@earendil-works/pi-agent-core";
import {streamSimple} from "@earendil-works/pi-ai/api/openai-responses";
import {createInterface} from "node:readline";
const lines=createInterface({input:process.stdin,crlfDelay:Infinity})[Symbol.asyncIterator]();
const send=value=>process.stdout.write(JSON.stringify(value)+"\n");
async function main(){
  const init=JSON.parse((await lines.next()).value);
  const model={api:"openai-responses",provider:"floodlab",id:init.model,name:init.model,
    baseUrl:init.base,reasoning:false,input:["text"],contextWindow:100000,maxTokens:2200,
    cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
  let requests=0,searchCount=0;
  const projectTool=(name,description,properties,required)=>({name,label:name,description,parameters:{type:"object",properties,required},execute:async(_id,args)=>{
    send({tool:name,args});const value=JSON.parse((await lines.next()).value);
    return {content:[{type:"text",text:JSON.stringify(value)}],details:{}};
  }});
  const s={type:"string"};
  const extra=init.context.project?[
    projectTool("find_events","Find EMS flood activations by English place or EMSR code and optional ISO dates.",{place:s,from_date:s,to_date:s},["place"]),
    projectTool("list_products","Inspect actual downloadable observed footprints, AOI names and acquisition dates for an EMSR code.",{code:s},["code"]),
    projectTool("search_satellite","Query official Sentinel-1 GRD and Sentinel-2 L2A metadata and create a coverage map, not an inundation analysis. Provide inclusive LOCAL ISO dates, UTC offset minutes, and a known lon/lat bbox [west,south,east,north] (optional for Putian/Xianyou).",{place:s,from_date:s,to_date:s,utc_offset_minutes:{type:"integer"},bbox:{type:"array",items:{type:"number"},minItems:4,maxItems:4}},["place","from_date","to_date","utc_offset_minutes"]),
    projectTool("get_footprint","Download one listed product to this chat's folder, record provenance and inspect geometry.",{key:s},["key"]),
    projectTool("analyse_footprint","Use QGIS on a downloaded product: union area, ranked grid intersections, map, PDF and project.",{key:s,grid_m:{type:"integer",enum:[500,1000,2000,5000]}},["key","grid_m"]),
    projectTool("sar_inventory","Identify the bounded Valencia Horta Sud Sentinel-1 pixel workflow and available date choices; this does not read pixels.",{},[]),
    projectTool("karla_inventory","Inspect the real Karla Lake EMSN184 recovery-monitoring case, available historical observations and workspace IDs.",{},[]),
    projectTool("karla_acquire","Download windowed Karla pixels for the selected sensor and its matching pre-event scene; returns actual valid coverage.",{sensor:{type:"string",enum:["sar","optical"]}},["sensor"]),
    projectTool("karla_segment","Run one selected Karla segmentation method and publish its candidate and observation masks and map. Record why method is suitable.",{download_id:s,algorithm:{type:"string",enum:["sar_change","optical_mndwi"]},reason:s},["download_id","algorithm","reason"]),
    projectTool("karla_compare","Compare the 18 Dec SAR mask and 19 Dec optical mask in common interpretable coverage and publish evidence and official reference agreement. Not a dynamics detector.",{sar_id:s,optical_id:s},["sar_id","optical_id"]),
    projectTool("karla_exposure","Overlay cropland and major roads on Karla evidence; export lists, maps and project, either whole study or north sector.",{comparison_id:s,scope:{type:"string",enum:["study","north"]}},["comparison_id","scope"]),
    projectTool("download_sar_pair","Query Sentinel-1 RTC STAC and download real VV and VH pixel windows for two same-orbit dates. Returns a workspace download ID.",{case:s,before:s,after:s},["case","before","after"]),
    projectTool("segment_sar_flood","Run multi-temporal radar change segmentation on the downloaded imagery, producing raster and vector candidates. Preset is balanced or conservative.",{download_id:s,preset:{type:"string",enum:["balanced","conservative"]}},["download_id","preset"]),
    projectTool("publish_sar_map","Summarize the candidate area by municipality, compare with earlier EMS extent, then produce QGIS map and portable project.",{analysis_id:s},["analysis_id"]),
    projectTool("transport_inventory","Read archived official Horta Sud EMS evidence and ICV municipality availability; find codes and road classes for named road segment screening.",{},[]),
    projectTool("screen_transport","Measure named or numbered road segments by municipality, separating flooded area and trace. [] means all towns. Choose a new output ID.",{product:s,town_codes:{type:"array",items:s},road_classes:{type:"array",items:s},output_id:s},["product","town_codes","road_classes","output_id"]),
    projectTool("export_transport_map","Export the newly completed traffic candidate analysis. Short English title (at most 68 characters); label 1 to 10 segments.",{analysis_id:s,map_title:s,top_n:{type:"integer",minimum:1,maximum:10}},["analysis_id","map_title","top_n"])
  ]:[];
  const agent=new Agent({initialState:{systemPrompt:init.system,model,tools:[
    {name:"search_floods",label:"检索洪涝资料",description:"Search recent flood catalog and news headlines. Use for current facts; result dates and source indices matter.",
     parameters:{type:"object",properties:{query:{type:"string"},iso:{type:"string"},days:{type:"integer"}},required:["query","iso","days"]},
     execute:async(_id,args)=>{
       if(++searchCount>2)throw new Error("Search budget exhausted; summarize available evidence.");
       send({tool:"search_floods",args});const result=JSON.parse((await lines.next()).value);
       return {content:[{type:"text",text:JSON.stringify(result)}],details:{}};
     }},...extra
  ]},toolExecution:"sequential",getApiKey:()=>init.key,streamFn:(m,c,o)=>{
    if(++requests>10)throw new Error("Pi turn budget exceeded");
    return streamSimple(m,c,{...o,maxTokens:2200,maxRetries:2,maxRetryDelayMs:8000,headers:{"User-Agent":"FloodLab-Pi/0.5"}});
  }});
  await agent.prompt(JSON.stringify(init.context));
  const last=agent.state.messages.at(-1);
  if(last?.role!=="assistant"||last.stopReason==="error"||last.stopReason==="aborted")throw new Error(last?.errorMessage||"Pi response incomplete");
  const raw=last.content.filter(c=>c.type==="text").map(c=>c.text).join("").trim();
  send({final:JSON.parse(raw.replace(/^```(?:json)?\s*|\s*```$/g,"")),requests});
}
main().then(()=>process.exit(0)).catch(e=>{send({error:String(e.message).slice(0,800)});process.exitCode=1;process.stdin.destroy();});
