// Capture the archived Workbench replies, without editing their contents.
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {readFile} from "node:fs/promises";

const require=createRequire(new URL("../desktop/package.json",import.meta.url));
const {chromium}=require("playwright");
const base=process.env.FLOODLAB_PREVIEW||"http://127.0.0.1:8781";
const evidence=JSON.parse(await readFile(new URL("./evidence/karla/karla.json",import.meta.url),"utf8"));
const browser=await chromium.launch({channel:"chrome",headless:true});
try{
  const page=await browser.newPage({viewport:{width:1120,height:1180},deviceScaleFactor:2});
  await page.goto(base,{waitUntil:"networkidle"});
  await page.locator("#historyList .history-item").filter({hasText:"Karla"}).first().click();
  await page.locator("#conversation .message.agent").nth(2).waitFor();
  for(const [index,name,lastTool] of [[1,"optical-update","karla_compare"],[2,"reuse-exposure","karla_exposure"]]){
    const reply=page.locator("#conversation .message.agent:has(.artifact-link)").nth(index);
    const question=reply.locator("xpath=preceding-sibling::div[contains(@class,'message user')][1]");
    const trace=reply.locator(".tool-summary");
    const artifacts=reply.locator(".artifact-link");
    assert.equal((await question.innerText()).trim(),evidence[index].question);
    assert.equal(await artifacts.count(),2);
    await trace.locator("summary").click();
    assert.ok((await trace.innerText()).includes(lastTool));
    const end=artifacts.last();
    await end.scrollIntoViewIfNeeded();
    const a=await trace.boundingBox();
    const b=await end.boundingBox();
    const first=await artifacts.first().boundingBox();
    const clip={x:Math.floor(a.x-10),y:Math.floor(a.y-8),
      width:Math.ceil(Math.max(first.width,b.width,340)+20),
      height:Math.ceil(b.y+b.height-a.y+9)};
    assert.ok(clip.y>=0&&clip.y+clip.height<=1180);
    await page.screenshot({path:new URL("./figures/karla-"+name+".png",import.meta.url).pathname,clip});
  }
}finally{
  await browser.close();
}
