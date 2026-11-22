import {_electron as electron} from "playwright";
import {writeFile} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
const here=path.dirname(fileURLToPath(import.meta.url));
let input="";for await(const chunk of process.stdin)input+=chunk.toString();
const key=input.trim();if(!key)throw new Error("A test key is required");
const checks=[];
const out=process.env.FLOODLAB_QA_OUT||path.join(here,"qa");
const app=await electron.launch({...(process.env.FLOODLAB_APP?{executablePath:process.env.FLOODLAB_APP}:{}),
  args:process.env.FLOODLAB_APP?[]:[here],env:{...process.env,FLOODLAB_USER_DATA:process.env.FLOODLAB_USER_DATA||path.join(here,"qa/fresh-user")},timeout:60000});
try{
  const page=await app.firstWindow();
  await page.waitForURL("http://127.0.0.1:*/",{timeout:180000});
  await page.locator("#example").waitFor();
  for(const model of ["gpt-6.1-sol","gpt-6.2-sol"]){
    await page.click("#connectionButton");
    await page.fill("#modelName",model);await page.fill("#apiKey",key);
    await page.locator("#connectionForm button[type=submit]").click();
    await page.waitForFunction(()=>!document.querySelector("#connectionDialog").open ||
      (document.querySelector("#connectionError").textContent && document.querySelector("#connectionError").textContent!=="连接中…"),{},{timeout:210000});
    const ok=!await page.locator("#connectionDialog").evaluate(e=>e.open);
    checks.push({model,connected:ok,error:ok?null:await page.locator("#connectionError").innerText()});
    console.log(JSON.stringify(checks.at(-1)));
    if(!ok)throw new Error("Connection failed: "+model);
  }
  await page.click("#newTask");
  await page.fill("#question","在瓦伦西亚 Horta Sud 预装案例中，实际检查 roads 要素数量和坐标系，然后重投影为 EPSG:32630 并核对新坐标系。不要制图。");
  await page.click("#sendTask");
  await page.waitForFunction(()=>document.querySelector("#taskStatus").textContent.includes("正在执行"));
  await page.waitForFunction(()=>document.querySelector("#taskStatus").textContent==="就绪",{},{timeout:300000});
  const runId=await page.evaluate(()=>state.runId);
  const run=await page.evaluate(id=>fetch("/api/run?id="+id).then(r=>r.json()),runId);
  const turn=run.turns.at(-1);
  checks.push({model:run.model,run:runId,status:run.status,calls:turn.calls.map(c=>({name:c.name,status:c.response.status})),answer:turn.answer});
  if(run.status!=="execution_finished_not_independently_evaluated" || !turn.calls.some(c=>c.name==="process"&&c.response.status==="ok"))throw new Error("Real agent/QGIS execution incomplete");
  await page.screenshot({path:path.join(out,"live-agent.png"),fullPage:true});
  await page.click("#connectionButton");
  await page.click("#disconnectButton");
  await page.locator("#connectionDialog .close-dialog").click();
  const status=await page.evaluate(()=>fetch("/api/status").then(r=>r.json()));
  if(status.configured)throw new Error("Credential was not cleared");
  checks.push({credentialCleared:true});
}finally{
  await writeFile(path.join(out,"live-checks.json"),JSON.stringify(checks,null,2).replaceAll(key,"[REDACTED]"));
  await app.close();
}
