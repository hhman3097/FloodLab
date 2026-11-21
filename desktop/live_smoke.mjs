import {_electron as electron} from "playwright";
import {writeFile} from "node:fs/promises";
import path from "node:path";
const here=path.dirname(new URL(import.meta.url).pathname);
let input="";for await(const chunk of process.stdin)input+=chunk.toString();
const key=input.trim();if(!key)throw new Error("A test key is required");
const checks=[];
const app=await electron.launch({args:[here],env:{...process.env,FLOODLAB_USER_DATA:path.join(here,"qa/fresh-user")},timeout:60000});
try{
  const page=await app.firstWindow();
  await page.waitForURL("http://127.0.0.1:*/",{timeout:180000});
  await page.waitForFunction(()=>document.querySelector("#gridCount")?.textContent==="42");
  for(const model of ["gpt-6.1-sol","gpt-6.2-sol"]){
    await page.click("#connectionButton");
    await page.fill("#modelName",model);await page.fill("#apiKey",key);
    await page.locator("#connectionForm button[type=submit]").click();
    await page.waitForFunction(()=>!document.querySelector("#connectionDialog").open ||
      (document.querySelector("#connectionError").textContent && document.querySelector("#connectionError").textContent!=="连接中…"),{},{timeout:210000});
    const ok=!await page.locator("#connectionDialog").evaluate(e=>e.open);
    checks.push({model,connected:ok,error:ok?null:await page.locator("#connectionError").innerText()});
    console.log(JSON.stringify(checks.at(-1)));
    if(!ok)await page.locator("#connectionDialog .close-dialog").click();
  }
  await page.click("#newTask");
  await page.fill("#question","请调用工具检查 roads 图层的要素数量、坐标系，以及 highway 字段的三个示例值；然后把 roads 重投影为 EPSG:32630 并核对新的坐标系。不要制图，不要计算洪涝损毁。");
  await page.click("#sendTask");
  await page.waitForFunction(()=>document.querySelector("#taskStatus").textContent.includes("正在执行"));
  await page.waitForFunction(()=>document.querySelector("#taskStatus").textContent==="就绪",{},{timeout:300000});
  const runId=await page.locator("#runSelect").inputValue();
  const run=await page.evaluate(id=>fetch("/api/run?id="+id).then(r=>r.json()),runId);
  const turn=run.turns.at(-1);
  checks.push({model:run.model,run:runId,status:run.status,calls:turn.calls.map(c=>({name:c.name,status:c.response.status})),answer:turn.answer});
  if(run.status!=="execution_finished_not_independently_evaluated" || !turn.calls.some(c=>c.name==="process"&&c.response.status==="ok"))throw new Error("Real agent/QGIS execution incomplete");
  await page.screenshot({path:path.join(here,"qa/live-agent.png"),fullPage:true});
  await page.click("#connectionButton");
  await page.click("#disconnectButton");
  await page.locator("#connectionDialog .close-dialog").click();
  const status=await page.evaluate(()=>fetch("/api/status").then(r=>r.json()));
  if(status.configured)throw new Error("Credential was not cleared");
  checks.push({credentialCleared:true});
}finally{
  await writeFile(path.join(here,"qa/live-checks.json"),JSON.stringify(checks,null,2).replaceAll(key,"[REDACTED]"));
  await app.close();
}
