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
    projectTool("get_footprint","Download one listed product to this chat's folder, record provenance and inspect geometry.",{key:s},["key"]),
    projectTool("analyse_footprint","Use QGIS on a downloaded product: union area, ranked grid intersections, map, PDF and project.",{key:s,grid_m:{type:"integer",enum:[500,1000,2000,5000]}},["key","grid_m"])
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
    return streamSimple(m,c,{...o,maxTokens:2200,headers:{"User-Agent":"FloodLab-Pi/0.5"}});
  }});
  await agent.prompt(JSON.stringify(init.context));
  const last=agent.state.messages.at(-1);
  if(last?.role!=="assistant"||last.stopReason==="error"||last.stopReason==="aborted")throw new Error(last?.errorMessage||"Pi response incomplete");
  const raw=last.content.filter(c=>c.type==="text").map(c=>c.text).join("").trim();
  send({final:JSON.parse(raw.replace(/^```(?:json)?\s*|\s*```$/g,"")),requests});
}
main().then(()=>process.exit(0)).catch(e=>{send({error:String(e.message).slice(0,800)});process.exitCode=1;process.stdin.destroy();});
