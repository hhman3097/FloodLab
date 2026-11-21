import {createRequire} from "node:module";
import {mkdir,writeFile} from "node:fs/promises";
import {execFileSync} from "node:child_process";
const require=createRequire(import.meta.url);
const {chromium}=require("/Users/frank/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const out=new URL("./qa/",import.meta.url);
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const page=await browser.newPage({viewport:{width:1440,height:960},deviceScaleFactor:1});
const errors=[];
page.on("pageerror",e=>errors.push(e.message));
const assertions=[];
function check(name,ok){assertions.push({name,passed:!!ok});if(!ok)throw new Error(name);}
try{
  await page.goto("http://127.0.0.1:8765",{waitUntil:"networkidle"});
  await page.waitForFunction(()=>document.querySelector("#gridCount").textContent==="42");
  check("initial verified grid",await page.locator("#verification").innerText().then(t=>t.includes("42")));
  check("initial total length",await page.locator("#totalLength").innerText().then(t=>t.includes("56.85")));
  const fractional=JSON.parse(execFileSync(".tools/qgis/bin/ogr2ogr",[
    "-f","GeoJSON","/vsistdout/",
    "event/runs/gis-comparison/generic/grid_ps_2km_exposure.gpkg"],{encoding:"utf8"}));
  const normalized=await page.evaluate(data=>{
    const before=JSON.stringify(data),result=displayStats(data);
    return {rate:result.features.find(f=>f.properties.id===38).properties.exposure_pct,
      empty:result.features.filter(f=>f.properties.total_m===0).every(f=>f.properties.exposure_pct===null),
      unchanged:before===JSON.stringify(data),metric:isRateMap({field:"exposure_rate"})};
  },fractional);
  check("real fraction layer displays correct percent",Math.abs(normalized.rate-35.230701201617)<1e-7);
  check("zero denominator remains null",normalized.empty);
  check("display normalization preserves source",normalized.unchanged&&normalized.metric);
  await page.screenshot({path:new URL("desktop.png",out).pathname,fullPage:true});
  const options=await page.locator("#versionSelect option").evaluateAll(es=>es.map(e=>e.value));
  await page.selectOption("#versionSelect",options[0]);
  await page.waitForFunction(()=>document.querySelector("#gridCount").textContent==="156");
  check("historical version marked",await page.locator("#versionBadge").innerText()==="历史版本");
  await page.click('[data-metric="exposure_pct"]');
  check("metric switch",await page.locator("#rankingUnit").innerText()==="按暴露率排序");
  await page.locator("#ranking tr").first().click();
  check("map popup",await page.locator(".leaflet-popup-content").isVisible());
  await page.click("#fitMap");
  await page.check("#showRoads");
  await page.waitForTimeout(700);
  await page.uncheck("#showSatellite");
  await page.check("#showSatellite");
  await page.click("#previewMap");
  await page.waitForFunction(()=>document.querySelector("#mapPreview").naturalWidth>1000);
  check("map preview opens",await page.locator("#mapDialog").isVisible());
  await page.locator("#mapDialog .close-dialog").click();
  const pdf=await page.locator("#downloadPDF").getAttribute("href");
  const download=await page.request.get("http://127.0.0.1:8765"+pdf);
  check("PDF download",download.ok()&&(await download.body()).subarray(0,4).toString()==="%PDF");
  await page.setViewportSize({width:1024,height:768});
  await page.click('.mobile-nav [data-panel="results"]');
  check("tablet results accessible",await page.locator("#totalLength").isVisible());
  await page.click('.mobile-nav [data-panel="map"]');
  check("tablet map accessible",await page.locator("#map").isVisible());
  await page.setViewportSize({width:390,height:844});
  await page.click('.mobile-nav [data-panel="map"]');
  await page.waitForTimeout(250);
  check("mobile has no horizontal overflow",await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:new URL("mobile-map.png",out).pathname,fullPage:true});
  await page.click('.mobile-nav [data-panel="results"]');
  check("mobile results available",await page.locator("#totalLength").isVisible());
  await page.screenshot({path:new URL("mobile-results.png",out).pathname,fullPage:true});
  await page.click('.mobile-nav [data-panel="chat"]');
  check("mobile composer available",await page.locator("#question").isVisible());
  if(process.argv.includes("--submit")){
    await page.fill("#question","沿用 Horta Sud 研究区和 EMS 洪涝轮廓，只统计 residential 住宅道路，网格改为 500 m。重算总长度、暴露长度和暴露率，分别用 total_m、exposed_m、exposure_pct 保存，按暴露率给出前五并制作新的专题图。");
    await page.click("#sendTask");
    await page.waitForFunction(()=>document.querySelector("#taskStatus").textContent.includes("正在执行"),{timeout:10000});
    check("real task submitted",true);
  }else{
    await page.click("#newTask");
    check("new task clears old downloads",await page.locator("#downloadPDF").evaluate(e=>e.classList.contains("disabled")));
    check("new task clears old metrics",await page.locator("#totalLength").innerText()==="—");
  }
  check("no browser runtime errors",errors.length===0);
}finally{
  await writeFile(new URL("checks.json",out),JSON.stringify({assertions,errors},null,2));
  await browser.close();
}
console.log(JSON.stringify({assertions,errors},null,2));
