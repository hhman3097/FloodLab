import assert from "node:assert/strict";
import {chromium} from "playwright";

// Real renderer, controlled job and artifact responses; no credentials or mutable GIS data.
const base=process.env.FLOODLAB_PREVIEW||"http://127.0.0.1:8770";
const b=await chromium.launch({channel:"chrome"});const p=await b.newPage();
const messages=[];p.on("pageerror",e=>messages.push(e.message));
let submitted=false;
try{
  await p.route("**/api/status",r=>r.fulfill({json:{configured:true,model:"test",runs:submitted?["fixture"]:[],
    default_run:"fixture",jobs:submitted?[{id:"j",run_id:"fixture",status:"finished"}]:[]}}));
  await p.route("**/api/task",r=>{submitted=true;return r.fulfill({json:{job_id:"j",run_id:"fixture"}});});
  await p.route("**/api/run?*",r=>r.fulfill({json:{id:"fixture",turns:[{question:"快速任务",answer:"保留的历史回答",calls:[]}],
    artifacts:[{id:"map",files:["pdf"],layer_available:false,layer_id:"gone",spec:{field:"exposed_m",title:"仍可取回的 PDF"}}],verification:[null]}}));
  await p.goto(base);await p.waitForFunction(()=>document.querySelector("#connectionStatus").classList.contains("ready"));
  await p.fill("#question","快速任务");await p.click("#sendTask");
  await p.locator(".message.agent .answer-prose").getByText("保留的历史回答").waitFor();
  await p.locator('.view-nav button[data-panel="results"]').click();
  assert.match(await p.locator("#verification").innerText(),/丢失/);
  assert.match(await p.locator("#downloadPDF").getAttribute("href"),/kind=pdf/);
  assert.equal(await p.locator("#downloadData").getAttribute("href"),null);
  assert.equal(await p.locator("#previewMap").isDisabled(),true);
  assert.equal(await p.locator("#gridCount").textContent(),"—");
  assert.deepEqual(messages,[]);
  console.log("PASS: immediate completion, readable history, remaining PDF, missing layer/preview disabled.");
}finally{await b.close();}
