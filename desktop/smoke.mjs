import {_electron as electron} from "playwright";
import {mkdir, writeFile} from "node:fs/promises";
import path from "node:path";
const here=path.dirname(new URL(import.meta.url).pathname);
const dir=process.env.FLOODLAB_USER_DATA || path.join(here,"qa","fresh-user");
const out=path.join(here,"qa");
await mkdir(out,{recursive:true});
const args=process.env.FLOODLAB_APP ? [] : [here];
const app=await electron.launch({
  ...(process.env.FLOODLAB_APP ? {executablePath:process.env.FLOODLAB_APP} : {}),
  args, env:{...process.env,FLOODLAB_USER_DATA:dir}, timeout:60000,
});
const checks=[];
function check(name,ok){checks.push({name,passed:!!ok});if(!ok)throw new Error(name);}
try{
  const page=await app.firstWindow();
  const errors=[];
  page.on("pageerror",e=>errors.push(e.message));
  await page.waitForURL("http://127.0.0.1:*/",{timeout:600000});
  await page.locator("#example").waitFor();
  check("new conversation is initial view",await page.locator("#welcome").isVisible());
  await page.screenshot({path:path.join(out,"studio-home.png")});
  await page.click("#example");
  await page.click('.view-nav [data-panel="results"]');
  await page.waitForFunction(()=>document.querySelector("#gridCount")?.textContent==="42",{},{timeout:60000});
  check("bundled runtime and seed case start",true);
  const status=await page.evaluate(()=>fetch("/api/status").then(r=>r.json()));
  check("no key is shipped",!status.configured);
  check("sample numerics",await page.locator("#totalLength").innerText().then(s=>s.includes("56.85")));
  check("desktop QGIS control",await page.locator("#openQGIS").isVisible());
  await page.screenshot({path:path.join(out,"desktop.png"),fullPage:true});
  const versions=await page.locator("#versionSelect option").evaluateAll(es=>es.map(e=>e.value));
  await page.click('.view-nav [data-panel="map"]');
  await page.selectOption("#versionSelect",versions[0]);
  await page.waitForFunction(()=>document.querySelector("#gridCount").textContent==="156");
  await page.click('.view-nav [data-panel="results"]');
  check("historical version",await page.locator("#versionBadge").innerText()==="历史版本");
  await page.locator("#ranking tr").first().click();
  check("real map feature interaction",await page.locator(".leaflet-popup-content").isVisible());
  await page.click('.view-nav [data-panel="results"]');
  await page.click("#previewMap");
  await page.waitForFunction(()=>document.querySelector("#mapPreview").naturalWidth>1000);
  check("QGIS map raster renders",true);
  await page.screenshot({path:path.join(out,"map-preview.png"),fullPage:true});
  await page.locator("#mapDialog .close-dialog").click();
  const pdfHref=await page.locator("#downloadPDF").getAttribute("href");
  const pdf=await page.evaluate(async href=>{const r=await fetch(href);return {ok:r.ok,bytes:[...new Uint8Array(await r.arrayBuffer()).slice(0,4)]};},pdfHref);
  check("PDF export",pdf.ok&&String.fromCharCode(...pdf.bytes)==="%PDF");
  const rejection=await fetch(page.url()+"api/status");
  check("untrusted local HTTP request rejected",rejection.status===403);
  await page.click('.view-nav [data-panel="chat"]');
  await page.click("#connectionButton");
  check("editable endpoint",await page.locator("#baseURL").isEditable());
  check("editable model",await page.locator("#modelName").isEditable());
  check("6.2 and 6.1 presets",await page.locator("#modelOptions option").count()===2);
  check("encrypted key opt-in defaults off",!await page.locator("#rememberKey").isChecked());
  await page.screenshot({path:path.join(out,"settings.png"),fullPage:true});
  await page.locator("#connectionDialog .close-dialog").click();
  const state=await app.evaluate(()=>({node:process.versions.node,electron:process.versions.electron,platform:process.platform,arch:process.arch}));
  check("native Electron process",state.platform==="darwin"&&state.arch==="arm64");
  check("no renderer errors",errors.length===0);
  await writeFile(path.join(out,"checks.json"),JSON.stringify({checks,state,errors,userData:dir,scope:"Real desktop execution on this Mac; no Windows execution claimed."},null,2));
  console.log(JSON.stringify({checks,state},null,2));
}finally{
  await writeFile(path.join(out,"last-checks.json"),JSON.stringify(checks,null,2));
  await app.close();
}
