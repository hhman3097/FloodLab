const {contextBridge, ipcRenderer} = require("electron");
contextBridge.exposeInMainWorld("floodlab", {
  chooseFolder:()=>ipcRenderer.invoke("choose-folder"),
  openFolder:run=>ipcRenderer.invoke("open-folder",run),
  saveConnection: value => ipcRenderer.invoke("save-connection", value),
  forgetConnection: () => ipcRenderer.invoke("forget-connection"),
  openProject: value => ipcRenderer.invoke("open-project", value),
  onStartup: callback => ipcRenderer.on("startup", (_event, text) => callback(text)),
  onStartupFailed: callback => ipcRenderer.on("startup-failed", () => callback()),
  retryStartup: () => ipcRenderer.send("retry-startup"),
});
