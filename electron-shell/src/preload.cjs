const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("qrSuite", {
  getInfo: () => ipcRenderer.invoke("app:info"),
  openFile: (options) => ipcRenderer.invoke("dialog:openFile", options),
  openDirectory: () => ipcRenderer.invoke("dialog:openDirectory"),
  saveFile: (options) => ipcRenderer.invoke("dialog:saveFile", options),
  controlWindow: (action) => ipcRenderer.invoke("window:control", action),
  isWindowFullScreen: () => ipcRenderer.invoke("window:isFullScreen"),
  runTask: (payload) => ipcRenderer.invoke("task:run", payload),
  openPlayer: (payload) => ipcRenderer.invoke("player:open", payload),
  openSystemPlayer: (filePath) => ipcRenderer.invoke("player:openSystem", filePath),
  listRecordingMonitors: () => ipcRenderer.invoke("recording:listMonitors"),
  startRecording: (payload) => ipcRenderer.invoke("recording:start", payload),
  stopRecording: (runId) => ipcRenderer.invoke("recording:stop", runId),
  openLogsDirectory: () => ipcRenderer.invoke("logs:openDirectory"),
  writeAppLog: (payload) => ipcRenderer.invoke("logs:write", payload),
  copyText: (text) => ipcRenderer.invoke("clipboard:writeText", text),
  encodeTextQr: (payload) => ipcRenderer.invoke("text:encode", payload),
  decodeTextQr: (payload) => ipcRenderer.invoke("text:decode", payload),
  onTaskLog: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("task:log", listener);
    return () => ipcRenderer.removeListener("task:log", listener);
  },
  onTaskDone: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("task:done", listener);
    return () => ipcRenderer.removeListener("task:done", listener);
  }
});
