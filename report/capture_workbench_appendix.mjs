import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(new URL("../desktop/package.json", import.meta.url));
const { chromium } = require("playwright");
const karlaURL = process.env.FLOODLAB_KARLA_PREVIEW || "http://127.0.0.1:8781";
const extraURL = process.env.FLOODLAB_CASES_PREVIEW || "http://127.0.0.1:8774";
const karla = JSON.parse(await readFile(new URL("evidence/karla/karla.json", import.meta.url), "utf8"));
const crete = JSON.parse(await readFile(new URL("evidence/monitoring/runs/crete.json", import.meta.url), "utf8"));
const out = new URL("figures/workbench-appendix/", import.meta.url);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });

function destination(name) {
  return fileURLToPath(new URL(name + ".png", out));
}

async function thread(page, term) {
  await page.locator("#historyList .history-item").filter({ hasText: term }).first().click();
  await page.locator("#conversation .message.agent .artifact-link").first().waitFor();
}

async function captureTurn(page, record, name) {
  const matches = page.locator(".message.user").filter({ hasText: record.question });
  const user = record.turn.event_outputs.length
    ? matches.locator("xpath=self::div[following-sibling::div[1]//button[contains(@class, 'artifact-link')]]")
    : matches;
  const question = user.locator("p");
  const reply = user.locator("xpath=following-sibling::div[1]");
  const trace = reply.locator(".tool-summary");
  const artifacts = reply.locator(".artifact-link");
  assert.equal(await question.textContent(), record.question);
  assert.equal(await artifacts.count(), record.turn.event_outputs.length);
  if ((await trace.getAttribute("open")) === null) await trace.locator("summary").click();
  const visibleTrace = await trace.innerText();
  for (const call of record.turn.calls) assert.ok(visibleTrace.includes(call.name + " · ok"));

  await user.evaluate(node => {
    const scroller = node.closest("#conversation");
    scroller.scrollTop += node.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 8;
  });
  const top = await user.boundingBox();
  const bottom = await (record.turn.event_outputs.length ? artifacts.last() : trace).boundingBox();
  const panel = await page.locator("#conversation").boundingBox();
  assert.ok(top.y >= panel.y && bottom.y + bottom.height <= panel.y + panel.height);
  await page.screenshot({
    path: destination(name),
    clip: {
      x: Math.max(0, Math.floor(top.x - 8)),
      y: Math.floor(top.y - 5),
      width: Math.ceil(top.width + 16),
      height: Math.ceil(bottom.y + bottom.height - top.y + 12),
    },
  });
  return artifacts;
}

async function captureMap(page, button, name, files) {
  await button.click();
  const dialog = page.locator("#eventDialog");
  await dialog.waitFor({ state: "visible" });
  await page.waitForFunction(() => {
    const img = document.querySelector("#eventPreview");
    return img.complete && img.naturalWidth > 1000;
  });
  for (const file of files) assert.equal(await dialog.getByRole("link", { name: file }).count(), 1);
  const controls = await page.locator("#eventFiles").boundingBox();
  const bounds = await dialog.boundingBox();
  assert.ok(controls.y + controls.height <= bounds.y + bounds.height);
  await dialog.screenshot({ path: destination(name) });
  await page.keyboard.press("Escape");
}

try {
  const page = await browser.newPage({ viewport: { width: 1160, height: 700 }, deviceScaleFactor: 2 });
  await page.goto(karlaURL, { waitUntil: "networkidle" });
  await page.locator("#historyList .history-item").filter({ hasText: "Karla" }).first().waitFor();
  await page.locator("#newTask").click();
  await page.locator("#welcome").waitFor();
  await page.locator("#question").blur();
  await page.screenshot({ path: destination("workbench-home") });
  await page.locator("#projectFolder").click();
  const folder = page.locator("#folderDialog");
  await folder.waitFor({ state: "visible" });
  assert.equal(await folder.locator("input").inputValue(), "");
  await folder.screenshot({ path: destination("workspace-picker") });
  await page.keyboard.press("Escape");

  await page.setViewportSize({ width: 1160, height: 1240 });
  await thread(page, "Karla");
  for (const [index, record] of karla.entries()) {
    const artifacts = await captureTurn(page, record, "karla-turn-" + (index + 1));
    if (index === 0) {
      await captureMap(page, artifacts.first(), "karla-radar-map", ["专题图 PDF", "证据分类 GeoTIFF", "分析结果与口径", "完整 QGIS 工程包"]);
    }
    if (index === 2) {
      const resultTable = page.locator(".answer-table").filter({ hasText: "北部主要道路" });
      await resultTable.scrollIntoViewIfNeeded();
      await resultTable.evaluate(node => { node.scrollLeft = node.scrollWidth; });
      await resultTable.screenshot({ path: destination("karla-table-scroll") });
      const north = artifacts.filter({ hasText: "北部片区核查" });
      assert.equal(await north.count(), 1);
      await captureMap(page, north, "karla-north-exports", ["核查图 PDF", "农田核查 CSV", "路段清单 CSV", "完整 QGIS 工程包"]);
    }
  }

  await page.setViewportSize({ width: 1160, height: 1030 });
  await page.goto(extraURL, { waitUntil: "networkidle" });
  await thread(page, "Kastelli");
  await captureTurn(page, crete[0], "kastelli-catalog");
  await captureMap(page, page.locator("#conversation .artifact-link").filter({ hasText: "Kastelli" }).last(),
    "kastelli-downloads", ["专题图 PDF", "网格 GeoJSON", "面积与排名", "QGIS 工程"]);
} finally {
  await browser.close();
}
