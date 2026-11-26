import {createRequire} from "node:module";
import {mkdir,readFile,writeFile} from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const require=createRequire(new URL("../desktop/package.json",import.meta.url));
const {chromium}=require("playwright");
const address=process.env.FLOODLAB_PREVIEW||"http://127.0.0.1:8774";
const out=path.resolve(process.argv[2]||"/tmp/floodlab-monitoring-browser");
const scenarios={
  karla:[
    ["给希腊 Karla 湖做一份截至 2023 年 12 月 18 日的洪灾恢复监测初报。实际获取合适的灾前和本次观测像元，由你判断可用的方法，提取候选积水和不可判读区，出一张初报图。",
     ["karla_inventory","karla_acquire","karla_segment"],"karla_evidence"],
    ["现在更新到 12 月 19 日。实际取得当天可用的新影像，说明你为什么选这次的算法。和昨天的初报比较，分开两期都有证据、单期有证据和不可判读，保留初报并出差异图。","karla_acquire karla_segment karla_compare".split(" "),"karla_evidence"],
    ["请用刚才的证据图做核查简报：统计整个研究窗及北部片区的农田和主要道路，区分两期都有证据、仅一期和未知，给地图与可复核清单。不重新下载或分割，保留前面的图。",["karla_exposure"],"karla_exposure"],
  ],
  sar:[
    ["用灾前灾后卫星影像做一份2024年瓦伦西亚 Horta Sud 洪涝遥感分析。查合适的 Sentinel-1 影像，实际下载像元、提取候选淹没范围，算各市镇的面积，说明和已有官方产品一致与不一致的范围，并出 QGIS 专题图；不要拿现成边界冒充影像分割。",
     ["sar_inventory","download_sar_pair","segment_sar_flood","publish_sar_map"],"sar_flood_change"],
    ["还是用刚才下载的两期影像，不要重新下载。换保守一点的分割规则，比较候选面积，并给我新地图，保留之前那张。",
     ["segment_sar_flood","publish_sar_map"],"sar_flood_change"],
  ],
  transport:[
    ["请对2024年瓦伦西亚 Horta Sud 洪灾做分市镇的主要道路核查，给出具名或带编号的候选路段、证据和 QGIS 图。注意区分淹没区与洪水痕迹，标出需额外核查的桥隧。不要用规则网格。",
     ["transport_inventory","screen_transport","export_transport_map"],"transport_screening"],
    ["沿用刚才的观测和道路口径，只看 Paiporta 和 Benetússer。核对两个市镇的覆盖，再给我更新后的核查图和路段清单，保留上一张。",
     ["screen_transport","export_transport_map"],"transport_screening"],
    ["现在只看 Benetússer，观测产品不变，改查三级路和连接道、住宅道路以及未分级道路。单独出一张图，不要混入上一轮。",
     ["screen_transport","export_transport_map"],"transport_screening"],
  ],
  crete:[
    ["查2026年10月希腊克里特岛洪涝 EMSR933 的 Kastelli 观测产品，先只列可下载产品和观测日期，不要下载或统计。",["list_products"],null],
    ["下载刚才 Kastelli 的官方观测产品，合并量算面积，按一公里网格给出位置和地图。",["get_footprint","analyse_footprint"],"footprint"],
  ],
  putian:[
    ["查福建莆田、仙游 2026年9月3日至6日（中国当地时间）的 Sentinel-1 和 Sentinel-2 观测覆盖，给我影像足迹图，不要把足迹当作洪水范围。",["search_satellite"],"satellite_coverage"],
  ],
};
await mkdir(out,{recursive:true});
const browser=await chromium.launch({channel:"chrome",headless:true});
const errors=[];
const selected=process.argv.slice(3);
try{
  for(const [scenario,steps] of Object.entries(scenarios)){
    if(selected.length&&!selected.includes(scenario))continue;
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    page.on("pageerror", e=>errors.push(e.message));
    await page.goto(address,{waitUntil:"networkidle"});
    const resume=process.env.FLOODLAB_RESUME_EVIDENCE;
    const history=resume?JSON.parse(await readFile(resume,"utf8")).filter(r=>r.status==="execution_finished_not_independently_evaluated"):[];
    if(history.length){
      await page.locator("#historyList .history-item").first().click();
      await page.locator("#conversation .artifact-link").first().waitFor();
    }
    for(const [question,expected,kind] of steps.slice(history.length)){
      await page.locator("#question").fill(question);
      const sent=page.waitForResponse(r=>new URL(r.url()).pathname==="/api/task"&&r.request().method()==="POST");
      await page.locator("#sendTask").click();
      const reply=await sent;
      assert.ok(reply.ok(), await reply.text());
      const id=(await reply.json()).run_id;
      let data;
      const deadline=Date.now()+550000;
      while(Date.now()<deadline){
        const request=await page.request.get(address+"/api/run?id="+id);
        data=await request.json();
        if(data.status&&data.status!=="running")break;
        await new Promise(r=>setTimeout(r,1800));
      }
      const last=data.turns.at(-1);
      history.push({id,question,project:data.project,status:data.status,model:data.model,turn:last,event_outputs:data.event_outputs});
      await writeFile(path.join(out,scenario+".json"),JSON.stringify(history,null,2));
      assert.equal(data.status,"execution_finished_not_independently_evaluated",last.error||last.answer||"incomplete");
      const calls=last.calls.filter(c=>c.response.status==="ok").map(c=>c.name);
      for(const tool of expected)assert.ok(calls.includes(tool),scenario+": "+tool+" missing from "+calls);
      assert.ok(last.answer);
      if(!kind){assert.equal(data.event_outputs.length,0);continue;}
      const output=data.event_outputs.at(-1);
      assert.ok(output,scenario+": output missing");
      if(kind!=="footprint")assert.equal(output.kind,kind);
      const count=history.filter(h=>h.event_outputs.length).length;
      await page.waitForFunction(n=>document.querySelectorAll("#conversation .artifact-link").length>=n,count);
      const selectedMap=page.locator("#conversation .artifact-link").filter({hasText:output.aoi}).last();
      await selectedMap.click();
      await page.locator("#eventDialog").waitFor({state:"visible"});
      if(kind==="satellite_coverage"){
        await page.locator("#satelliteFootprints svg path").first().waitFor({state:"visible"});
      }else{
        await page.waitForFunction(folder=>{
          const img=document.querySelector("#eventPreview");
          return img.complete&&img.naturalWidth>1000&&new URL(img.currentSrc).searchParams.get("folder")===folder;
        },output.folder);
        const pdf=await page.locator("#eventFiles a").first().getAttribute("href");
        const response=await page.request.get(address+pdf);
        assert.ok(response.ok()&&(await response.body()).subarray(0,4).toString()==="%PDF");
        if(kind!=="footprint"){
          const bundle=await page.locator("#eventFiles a").last().getAttribute("href");
          const r=await page.request.get(address+bundle);
          assert.ok(r.ok()&&(await r.body()).subarray(0,2).toString()==="PK");
        }
      }
      await page.screenshot({path:path.join(out,scenario+"-"+history.length+"-map.png")});
      await page.keyboard.press("Escape");
      await page.screenshot({path:path.join(out,scenario+"-"+history.length+"-chat.png")});
      if(count>1){
        await page.locator("#conversation .artifact-link").first().click();
        await page.waitForFunction(()=>document.querySelector("#eventPreview").naturalWidth>1000);
        const src=await page.locator("#eventPreview").getAttribute("src");
        assert.equal(new URL(src,address).searchParams.get("folder"),history[0].event_outputs[0].folder);
        await page.keyboard.press("Escape");
      }
      console.log(scenario,history.length,calls.join(" -> "),output.folder);
    }
    if(scenario==="sar"){
      assert.equal(history[0].event_outputs[0].preset,"balanced");
      assert.equal(history[1].event_outputs[0].preset,"conservative");
      assert.ok(!history[1].turn.calls.some(c=>c.name==="download_sar_pair"));
      assert.equal(history[0].event_outputs[0].input,history[1].event_outputs[0].input);
    }
    await page.close();
  }
  assert.deepEqual(errors,[]);
}finally{
  await browser.close();
}
