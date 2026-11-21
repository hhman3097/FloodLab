const {app, BrowserWindow, ipcMain, Menu, dialog, safeStorage, session, shell} = require("electron");
const fs = require("node:fs");
const fsp = require("node:fs/promises");
const path = require("node:path");
const crypto = require("node:crypto");
const {spawn} = require("node:child_process");
const {pathToFileURL} = require("node:url");

if (process.env.FLOODLAB_USER_DATA) app.setPath("userData", path.resolve(process.env.FLOODLAB_USER_DATA));
const home = app.getPath("userData");
const resources = app.isPackaged ? process.resourcesPath : path.join(__dirname, "stage");
const bundle = app.isPackaged ? path.join(resources, "runtime-bundle") :
  path.join(resources, `${process.platform === "win32" ? "win" : "mac"}-${process.arch}`);
const payload = path.join(resources, "payload");
const workspace = path.join(home, "workspace");
let window, backend, origin, runtime, setupRunning = false, quitting = false;
const setupChildren = new Set();
const token = crypto.randomBytes(32).toString("hex");
const exists = p => fs.existsSync(p);
const readJSON = async p => JSON.parse(await fsp.readFile(p, "utf8"));
const logPath = path.join(home, "desktop.log");

async function log(text) {
  await fsp.mkdir(home, {recursive:true});
  await fsp.appendFile(logPath, `${new Date().toISOString()} ${text}\n`);
}

function progress(text) {
  if (window && !window.isDestroyed()) window.webContents.send("startup", text);
}

function execute(file, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(file, args, {windowsHide:true, detached:process.platform !== "win32", timeout:600000, ...options});
    setupChildren.add(child);
    let tail = "";
    for (const stream of [child.stdout, child.stderr]) stream?.on("data", data => {
      tail = (tail + data.toString()).slice(-12000);
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      setupChildren.delete(child);
      code === 0 ? resolve(tail) : reject(new Error(`${path.basename(file)} exited ${code ?? signal}\n${tail}`));
    });
  });
}

async function digest(file) {
  const hash = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}

async function prepareRuntime() {
  const manifest = await readJSON(path.join(bundle, "runtime.json"));
  runtime = path.join(home, "runtimes", manifest.id);
  if (exists(path.join(runtime, ".floodlab-ready"))) return;
  progress("正在校验内置 QGIS 组件…");
  const installer = path.join(bundle, process.platform === "win32" ? "micromamba.exe" : "micromamba");
  if (await digest(installer) !== manifest.installer_sha256) throw new Error("Runtime installer checksum mismatch");
  const specs = ["@EXPLICIT"];
  for (let i = 0; i < manifest.packages.length; i++) {
    const pkg = manifest.packages[i];
    const file = path.join(bundle, "packages", pkg.file);
    if (await digest(file) !== pkg.sha256) throw new Error(`Package checksum mismatch: ${pkg.file}`);
    specs.push(`${pathToFileURL(file).href}#${pkg.md5}`);
    if (i % 25 === 0) progress(`正在校验 QGIS 组件 ${i + 1}/${manifest.packages.length}…`);
  }
  await fsp.mkdir(path.dirname(runtime), {recursive:true});
  // An interrupted first installation is disposable; project data lives elsewhere.
  if (exists(runtime)) await fsp.rm(runtime, {recursive:true, force:true});
  const specPath = path.join(home, `runtime-${manifest.id}.txt`);
  await fsp.writeFile(specPath, specs.join("\n") + "\n");
  progress("正在离线安装内置 QGIS，首次启动需要几分钟…");
  const result = await execute(installer, [
    "create", "--no-rc", "--offline", "--yes", "--no-shortcuts",
    "--root-prefix", path.join(home, "package-cache"),
    "--prefix", runtime, "--file", specPath,
  ], {env:{...process.env, MAMBA_NO_BANNER:"1"}});
  await log(result);
  if (process.platform === "darwin") await signRelocatedBinaries();
  const probe = "import os; dll = os.add_dll_directory(os.path.join(os.environ['AI4S_QGIS_PREFIX'], 'Library', 'bin')) if os.name == 'nt' else None; from qgis.core import Qgis; print(Qgis.QGIS_VERSION)";
  await execute(python(), ["-c", probe], {env:runtimeEnv()});
  await fsp.writeFile(path.join(runtime, ".floodlab-ready"), manifest.id);
}

async function signRelocatedBinaries() {
  progress("正在准备 macOS 运行组件…");
  const binaries = [];
  const meta = path.join(runtime, "conda-meta");
  for (const name of await fsp.readdir(meta)) {
    if (!name.endsWith(".json")) continue;
    const record = await readJSON(path.join(meta, name));
    for (const item of record.paths_data?.paths || []) {
      if (item.path_type !== "hardlink" || !item.sha256_in_prefix || item.sha256 === item.sha256_in_prefix) continue;
      const file = path.join(runtime, item._path);
      const handle = await fsp.open(file, "r");
      const head = Buffer.alloc(4);
      try {await handle.read(head, 0, 4, 0);} finally {await handle.close();}
      if (["cffaedfe","cefaedfe","cafebabe","bebafeca"].includes(head.toString("hex"))) binaries.push(file);
    }
  }
  for (let start = 0; start < binaries.length; start += 30)
    await execute("/usr/bin/codesign", ["--force", "--sign", "-", ...binaries.slice(start,start+30)]);
  await log(`Ad-hoc signed ${binaries.length} Mach-O files changed by prefix relocation.`);
}

function python() {
  return process.platform === "win32" ? path.join(runtime, "python.exe") : path.join(runtime, "bin", "python");
}

function runtimeEnv(gui = false) {
  const win = process.platform === "win32";
  const library = win ? path.join(runtime, "Library") : runtime;
  const modules = win ? path.join(library, "python") : path.join(library, "share", "qgis", "python");
  const env = {...process.env, AI4S_QGIS_PREFIX:runtime, QGIS_PREFIX_PATH:library,
    PYTHONPATH:[modules, path.join(modules, "plugins")].join(path.delimiter),
    QT_PLUGIN_PATH:[path.join(library, "plugins"), path.join(library, "qtplugins")].join(path.delimiter),
    PROJ_DATA:path.join(library, "share", "proj"), GDAL_DATA:path.join(library, "share", "gdal"),
    PYTHONUTF8:"1", PYTHONUNBUFFERED:"1", PYTHONNOUSERSITE:"1",
    PATH:[path.join(library, "bin"), runtime, path.join(runtime, "Scripts"), process.env.PATH || ""].join(path.delimiter)};
  delete env.PYTHONHOME;
  delete env.AI4S_API_KEY;
  if (gui) delete env.QT_QPA_PLATFORM;
  else env.QT_QPA_PLATFORM = "offscreen";
  return env;
}

function rebase(value, oldRoot, newRoot) {
  if (typeof value === "string") return value.split(oldRoot + "/").join(newRoot.replaceAll("\\", "/") + "/");
  if (Array.isArray(value)) return value.map(x => rebase(x, oldRoot, newRoot));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,rebase(v,oldRoot,newRoot)]));
  return value;
}

async function prepareWorkspace() {
  const manifest = await readJSON(path.join(payload, "payload.json"));
  const fingerprint = await digest(path.join(payload, "payload.json"));
  const marker = path.join(workspace, ".floodlab-ready");
  if (exists(marker)) {
    if ((await fsp.readFile(marker, "utf8")).trim() === fingerprint) return;
    progress("正在更新应用组件，保留已有分析成果…");
    for (const item of manifest.files) {
      const ownedCode = item.path.startsWith("workbench/") ||
        (item.path.startsWith("event/") && /\.(py|cjs|txt)$/.test(item.path) && item.path.split("/").length === 2) ||
        item.path.startsWith("event/skills/") || item.path.startsWith("desktop/");
      if (!ownedCode) continue;
      const source = path.join(payload, item.path);
      if (await digest(source) !== item.sha256) throw new Error(`Application checksum mismatch: ${item.path}`);
      const dest = path.join(workspace, item.path);
      await fsp.mkdir(path.dirname(dest), {recursive:true});
      await fsp.copyFile(source, dest);
    }
    await fsp.writeFile(marker, fingerprint);
    return;
  }
  progress("正在准备示例地图和独立工作目录…");
  const pending = workspace + ".installing";
  await fsp.rm(pending, {recursive:true, force:true});
  await fsp.mkdir(pending, {recursive:true});
  for (const item of manifest.files) {
    const source = path.join(payload, item.path);
    if (await digest(source) !== item.sha256) throw new Error(`Application checksum mismatch: ${item.path}`);
    const dest = path.join(pending, item.path);
    await fsp.mkdir(path.dirname(dest), {recursive:true});
    await fsp.copyFile(source, dest);
    if (item.path.startsWith("event/runs/") && item.path.endsWith(".json")) {
      const updated = rebase(await readJSON(dest), manifest.original_root, workspace);
      await fsp.writeFile(dest, JSON.stringify(updated, null, 2));
    }
  }
  await fsp.writeFile(path.join(pending, ".floodlab-ready"), fingerprint);
  if (exists(workspace)) throw new Error("Existing workspace is incomplete; it was preserved. See desktop.log.");
  await fsp.rename(pending, workspace);
}

async function connection() {
  const file = path.join(home, "connection.json");
  if (!exists(file)) return {};
  const value = await readJSON(file);
  if (value.encrypted_key) {
    try {
      value.key = safeStorage.decryptString(Buffer.from(value.encrypted_key, "base64"));
    } catch {
      await log("Saved credential could not be decrypted; reconnect in settings.");
    }
  }
  delete value.encrypted_key;
  return value;
}

async function startBackend() {
  const config = await connection();
  progress("正在启动地图工作台…");
  backend = spawn(python(), [path.join(workspace, "workbench/server.py"), "--port", "0", "--desktop-stdin"], {
    cwd:workspace, env:{...runtimeEnv(), AI4S_DESKTOP_TOKEN:token, FLOODLAB_PI_NODE:process.execPath},
    stdio:["pipe", "pipe", "pipe"], windowsHide:true, detached:process.platform !== "win32",
  });
  backend.stdin.end(JSON.stringify(config) + "\n");
  await new Promise((resolve, reject) => {
    let output = "", settled = false;
    const timer = setTimeout(() => reject(new Error("Local service startup timed out")), 180000);
    backend.stdout.on("data", data => {
      output += data.toString();
      const match = output.match(/FloodLab: (http:\/\/127\.0\.0\.1:\d+)/);
      if (match && !settled) {settled = true; origin = match[1]; clearTimeout(timer); resolve();}
    });
    backend.stderr.on("data", data => {void log(data.toString().replaceAll(config.key || "\0", "[REDACTED]"));});
    backend.once("error", error => {clearTimeout(timer); reject(error);});
    backend.once("exit", code => {
      clearTimeout(timer);
      if (!settled) reject(new Error(`Local service exited ${code}; see ${logPath}`));
      else if (!quitting) dialog.showErrorBox("FloodLab 服务已停止", `退出代码 ${code}。请重新启动应用。日志：${logPath}`);
    });
  });
  session.defaultSession.webRequest.onBeforeSendHeaders({urls:[`${origin}/*`]}, (details, callback) => {
    callback({requestHeaders:{...details.requestHeaders, "X-FloodLab-Token":token}});
  });
  await window.loadURL(origin);
}

function trusted(event) {
  if (!origin || new URL(event.senderFrame.url).origin !== origin) throw new Error("Untrusted caller");
}

ipcMain.handle("save-connection", async (event, value) => {
  trusted(event);
  if (typeof value.key !== "string" || typeof value.base_url !== "string" || typeof value.model !== "string") throw new Error("Invalid connection");
  const data = {base_url:value.base_url, model:value.model};
  if (value.remember) {
    if (!safeStorage.isEncryptionAvailable()) throw new Error("系统加密存储不可用；请取消勾选保存密钥。");
    data.encrypted_key = safeStorage.encryptString(value.key).toString("base64");
  }
  const dest = path.join(home, "connection.json");
  await fsp.writeFile(dest + ".tmp", JSON.stringify(data), {mode:0o600});
  await fsp.rename(dest + ".tmp", dest);
});

ipcMain.handle("forget-connection", async event => {
  trusted(event);
  await fsp.rm(path.join(home, "connection.json"), {force:true});
});
ipcMain.handle("choose-folder",async event=>{
  trusted(event);
  const result=await dialog.showOpenDialog(window,{title:"选择此对话的工作区",properties:["openDirectory","createDirectory"]});
  return result.canceled?null:result.filePaths[0];
});
ipcMain.handle("open-folder",async (event,run)=>{
  trusted(event);
  const res=await fetch(origin+"/api/run?id="+encodeURIComponent(run),{headers:{"X-FloodLab-Token":token}});
  const info=await res.json();if(!res.ok||!info.project?.root)throw new Error("未知工作区");
  const error=await shell.openPath(info.project.root);if(error)throw new Error(error);
});

ipcMain.handle("open-project", async (event, value) => {
  trusted(event);
  const params = new URLSearchParams({run:value.run, artifact:value.artifact});
  const response = await fetch(`${origin}/api/desktop-project?${params}`, {headers:{"X-FloodLab-Token":token}});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Project unavailable");
  const bin = process.platform === "win32" ? path.join(runtime, "Library/bin/qgis.exe") : path.join(runtime, "bin/qgis");
  const child = spawn(bin, ["--project", body.path], {env:runtimeEnv(true), stdio:"ignore", detached:true});
  await new Promise((resolve, reject) => {child.once("spawn", resolve); child.once("error", reject);});
  child.unref();
});

async function setup() {
  if (setupRunning) return;
  setupRunning = true;
  try {
    await prepareRuntime();
    await prepareWorkspace();
    await startBackend();
  } catch (error) {
    await log(error.stack || String(error));
    progress(`启动失败：${error.message}\n日志：${logPath}`);
    if (window && !window.isDestroyed()) window.webContents.send("startup-failed");
  } finally {setupRunning = false;}
}

ipcMain.on("retry-startup", event => {
  if (event.senderFrame.url === pathToFileURL(path.join(__dirname, "splash.html")).href) void setup();
});

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => {if (window) {window.restore(); window.focus();}});
  app.whenReady().then(async () => {
    await fsp.mkdir(home, {recursive:true});
    window = new BrowserWindow({width:1440, height:960, minWidth:1000, minHeight:680,
      title:"FloodLab", backgroundColor:"#f5f7f8",
      webPreferences:{preload:path.join(__dirname,"preload.cjs"), contextIsolation:true, sandbox:true, nodeIntegration:false}});
    window.webContents.setWindowOpenHandler(({url}) => {
      try {const u=new URL(url);if(u.protocol==="https:"&&["www.gdacs.org","gdacs.org","news.google.com","www.chinanews.com.cn","www.chinanews.com"].includes(u.hostname))void shell.openExternal(u.href);} catch {}
      return {action:"deny"};
    });
    window.webContents.on("will-navigate", (event, url) => {if (!origin || new URL(url).origin !== origin) event.preventDefault();});
    session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      ...(process.platform === "darwin" ? [{label:"FloodLab", submenu:[{role:"about"}, {type:"separator"}, {role:"quit"}]}] : []),
      {label:"文件", submenu:[
        {label:"打开成果目录", click:() => shell.openPath(path.join(workspace, "event/runs"))},
        {label:"打开应用日志", click:() => shell.openPath(logPath)},
        {type:"separator"}, {role:"close"}]},
      {label:"编辑", submenu:[{role:"undo"}, {role:"redo"}, {type:"separator"}, {role:"cut"}, {role:"copy"}, {role:"paste"}, {role:"selectAll"}]},
      {label:"视图", submenu:[{role:"resetZoom"}, {role:"zoomIn"}, {role:"zoomOut"}, {role:"togglefullscreen"}]}
    ]));
    await window.loadFile(path.join(__dirname, "splash.html"));
    await setup();
  });
}

app.on("before-quit", () => {
  quitting = true;
  for (const child of [...setupChildren, backend]) if (child?.pid) {
    try {
      if (process.platform === "win32") spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], {windowsHide:true});
      else process.kill(-child.pid, "SIGTERM");
    } catch {}
  }
});
app.on("window-all-closed", () => app.quit());
