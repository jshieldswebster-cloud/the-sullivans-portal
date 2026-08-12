const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("vvLuxe", {
  getApiBase: () => ipcRenderer.invoke("get-api-base"),
});
