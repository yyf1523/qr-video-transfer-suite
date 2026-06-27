const { app, BrowserWindow, clipboard, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");
const { pathToFileURL } = require("url");

app.commandLine.appendSwitch("no-sandbox");
app.commandLine.appendSwitch("disable-setuid-sandbox");
app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("autoplay-policy", "no-user-gesture-required");

if (process.platform === "linux") {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("disable-gpu-rasterization");
  app.commandLine.appendSwitch("disable-dev-shm-usage");
  app.commandLine.appendSwitch("ozone-platform", "x11");
  app.commandLine.appendSwitch("use-gl", "swiftshader");
  app.commandLine.appendSwitch("disable-features", "UseOzonePlatform,Vulkan");
}

const isPackaged = app.isPackaged;
const shellRoot = path.resolve(__dirname, "..");
const repoRoot = isPackaged ? null : path.resolve(shellRoot, "..");
const toolsRoot = isPackaged ? path.join(process.resourcesPath, "tools") : path.resolve(repoRoot, "build", "electron-tools");
const activeRecordings = new Map();
const playerWindows = new Set();

const tools = {
  videoEncode: {
    title: "视频加码",
    devScript: ["scripts", "linux_encoder_hd.py"],
    packaged: {
      win32: ["win32-x64", "QRVideoEncoderCLI.exe"],
      linux: ["linux-x64", "QRVideoEncoderCLI"]
    }
  },
  videoDecode: {
    title: "视频解码",
    devScript: ["scripts", "win_decoder_hd.py"],
    packaged: {
      win32: ["win32-x64", "QRVideoDecoderCLI.exe"],
      linux: ["linux-x64", "QRVideoDecoderCLI"]
    }
  },
  textQr: {
    title: "文本二维码",
    devScript: ["scripts", "text_qr_clipboard.py"],
    packaged: {
      win32: ["win32-x64", "QRTextClipboardCLI.exe"],
      linux: ["linux-x64", "QRTextClipboardCLI"]
    }
  }
};

function platformToolPath(tool) {
  const relative = tool.packaged[process.platform];
  if (!relative) {
    return null;
  }
  return path.join(toolsRoot, ...relative);
}

function devScriptPath(tool) {
  return repoRoot ? path.join(repoRoot, ...tool.devScript) : null;
}

function resolveCommand(toolId) {
  const tool = tools[toolId];
  if (!tool) {
    throw new Error(`Unknown tool: ${toolId}`);
  }

  const packagedPath = platformToolPath(tool);
  if (packagedPath && fs.existsSync(packagedPath)) {
    return { command: packagedPath, prefixArgs: [], mode: "packaged" };
  }

  const script = devScriptPath(tool);
  if (script && fs.existsSync(script)) {
    return {
      command: process.platform === "win32" ? "python" : "python3",
      prefixArgs: [script],
      mode: "dev"
    };
  }

  throw new Error(`${tool.title} CLI 未找到。`);
}

function normalizeArgs(args) {
  if (!Array.isArray(args)) {
    return [];
  }
  return args.filter((item) => item !== undefined && item !== null && String(item) !== "").map((item) => String(item));
}

function spawnTool(toolId, args, sender, runId, lifecycle = {}) {
  const resolved = resolveCommand(toolId);
  const fullArgs = [...resolved.prefixArgs, ...normalizeArgs(args)];
  const child = spawn(resolved.command, fullArgs, {
    cwd: repoRoot || app.getPath("documents"),
    windowsHide: true,
    env: {
      ...process.env,
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1"
    }
  });

  const emit = (stream, data) => {
    sender.send("task:log", {
      runId,
      stream,
      text: data.toString("utf8")
    });
  };

  child.stdout.on("data", (data) => emit("stdout", data));
  child.stderr.on("data", (data) => emit("stderr", data));
  let errorMessage = null;
  child.on("error", (error) => {
    errorMessage = error.message;
  });
  child.on("close", (code) => {
    lifecycle.onClose?.(code, errorMessage);
    if (!sender.isDestroyed()) {
      sender.send("task:done", { runId, code: errorMessage ? 1 : code, error: errorMessage });
    }
  });

  return {
    runId,
    pid: child.pid,
    command: resolved.command,
    args: fullArgs,
    mode: resolved.mode
  };
}

function workingDirectory() {
  return repoRoot || app.getPath("documents");
}

function resolveWorkingPath(filePath) {
  const rawPath = String(filePath || "").trim();
  if (!rawPath) {
    return null;
  }
  return path.isAbsolute(rawPath) ? path.normalize(rawPath) : path.resolve(workingDirectory(), rawPath);
}

function requireExistingFile(filePath) {
  const resolved = resolveWorkingPath(filePath);
  if (!resolved || !fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) {
    throw new Error("请先选择存在的视频文件。");
  }
  return resolved;
}

function runCapture(toolId, args) {
  const resolved = resolveCommand(toolId);
  const fullArgs = [...resolved.prefixArgs, ...normalizeArgs(args)];
  return new Promise((resolve, reject) => {
    const child = spawn(resolved.command, fullArgs, {
      cwd: repoRoot || app.getPath("documents"),
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        PYTHONUTF8: "1"
      }
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (data) => {
      stdout += data.toString("utf8");
    });
    child.stderr.on("data", (data) => {
      stderr += data.toString("utf8");
    });
    child.on("error", reject);
    child.on("close", (code) => {
      resolve({ code, stdout, stderr, command: resolved.command, args: fullArgs });
    });
  });
}

function imageDataUrl(filePath) {
  const data = fs.readFileSync(filePath);
  const ext = path.extname(filePath).toLowerCase();
  const mime = ext === ".jpg" || ext === ".jpeg" ? "image/jpeg" : "image/png";
  return `data:${mime};base64,${data.toString("base64")}`;
}

function appIconPath() {
  const iconFile = process.platform === "win32" ? "app-icon.ico" : "app-icon-256.png";
  return path.resolve(shellRoot, "assets", iconFile);
}

function createWindow() {
  const windowOptions = {
    width: 1180,
    height: 780,
    minWidth: 980,
    minHeight: 680,
    frame: false,
    title: "源码视频加码解码器",
    backgroundColor: "#f1f2f7",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false
    }
  };
  if (process.platform === "win32" || process.env.QR_ENABLE_LINUX_WINDOW_ICON === "1") {
    windowOptions.icon = appIconPath();
  }
  const win = new BrowserWindow(windowOptions);
  win.removeMenu();
  win.loadFile(path.join(__dirname, "index.html"));
}

function createPlayerWindow(payload = {}) {
  const mediaPath = requireExistingFile(payload.filePath);
  const player = new BrowserWindow({
    width: 1280,
    height: 760,
    minWidth: 720,
    minHeight: 480,
    frame: false,
    fullscreen: Boolean(payload.fullscreen),
    backgroundColor: "#000000",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false
    }
  });
  playerWindows.add(player);
  player.on("closed", () => playerWindows.delete(player));
  player.loadFile(path.join(__dirname, "player.html"), {
    query: {
      src: pathToFileURL(mediaPath).href,
      title: path.basename(mediaPath),
      autoplay: payload.autoplay === false ? "0" : "1",
      loop: payload.loop === false ? "0" : "1"
    }
  });
  return { opened: true, filePath: mediaPath };
}

function handleWindowControl(event, action) {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) {
    return false;
  }

  if (action === "minimize") {
    win.minimize();
    return true;
  }
  if (action === "maximize") {
    if (win.isMaximized()) {
      win.unmaximize();
    } else {
      win.maximize();
    }
    return true;
  }
  if (action === "close") {
    win.close();
    return true;
  }
  if (action === "toggle-fullscreen") {
    win.setFullScreen(!win.isFullScreen());
    return win.isFullScreen();
  }
  return false;
}

ipcMain.handle("app:info", () => ({
  version: app.getVersion(),
  electron: process.versions.electron,
  platform: process.platform,
  packaged: isPackaged,
  toolsRoot
}));

ipcMain.handle("dialog:openFile", async (_event, options = {}) => {
  const result = await dialog.showOpenDialog({
    properties: ["openFile"],
    filters: options.filters || [{ name: "All files", extensions: ["*"] }]
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:openDirectory", async () => {
  const result = await dialog.showOpenDialog({ properties: ["openDirectory"] });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:saveFile", async (_event, options = {}) => {
  const result = await dialog.showSaveDialog({
    defaultPath: options.defaultPath,
    filters: options.filters || [{ name: "All files", extensions: ["*"] }]
  });
  return result.canceled ? null : result.filePath;
});

ipcMain.handle("window:control", handleWindowControl);
ipcMain.handle("window:isFullScreen", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  return Boolean(win?.isFullScreen());
});

ipcMain.handle("task:run", (event, payload) => {
  const runId = payload.runId || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return spawnTool(payload.toolId, payload.args, event.sender, runId);
});

ipcMain.handle("player:open", (_event, payload) => createPlayerWindow(payload));

ipcMain.handle("player:openSystem", async (_event, filePath) => {
  const mediaPath = requireExistingFile(filePath);
  const error = await shell.openPath(mediaPath);
  if (error) {
    throw new Error(error);
  }
  return true;
});

ipcMain.handle("recording:listMonitors", async () => {
  const result = await runCapture("videoDecode", ["--list-record-monitors"]);
  if (result.code !== 0) {
    throw new Error(result.stderr.trim() || result.stdout.trim() || "无法读取屏幕列表。");
  }
  const line = result.stdout.split(/\r?\n/).find((item) => item.startsWith("[MONITORS] "));
  if (!line) {
    throw new Error("录屏器未返回屏幕列表。");
  }
  return JSON.parse(line.slice("[MONITORS] ".length));
});

ipcMain.handle("recording:start", (event, payload = {}) => {
  const runId = payload.runId || `record-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  if (activeRecordings.has(runId)) {
    throw new Error("该录屏任务已在运行。");
  }

  const fps = Number(payload.fps || 30);
  const monitor = Number(payload.monitor ?? 1);
  if (!Number.isFinite(fps) || fps < 1 || fps > 60) {
    throw new Error("录屏帧率必须在 1 到 60 之间。");
  }
  if (!Number.isInteger(monitor) || monitor < 0) {
    throw new Error("屏幕编号无效。");
  }

  const timestamp = new Date().toISOString().replace(/[-:]/g, "").replace(/T/, "-").slice(0, 15);
  const output = resolveWorkingPath(payload.output) || path.join(workingDirectory(), "screen_recordings", `screen-record-${timestamp}.mp4`);
  const stopFile = path.join(os.tmpdir(), `qr-video-record-stop-${runId}.flag`);
  fs.rmSync(stopFile, { force: true });

  const args = [
    "--record-screen",
    "--record-only",
    "--record-output",
    output,
    "--record-fps",
    String(fps),
    "--record-monitor",
    String(monitor),
    "--record-stop-file",
    stopFile
  ];
  if (String(payload.region || "").trim()) {
    args.push("--record-region", String(payload.region).trim());
  }

  activeRecordings.set(runId, { stopFile, output, stopping: false });
  try {
    const task = spawnTool("videoDecode", args, event.sender, runId, {
      onClose: () => {
        activeRecordings.delete(runId);
        fs.rmSync(stopFile, { force: true });
      }
    });
    return { ...task, output };
  } catch (error) {
    activeRecordings.delete(runId);
    fs.rmSync(stopFile, { force: true });
    throw error;
  }
});

ipcMain.handle("recording:stop", (_event, runId) => {
  const recording = activeRecordings.get(String(runId || ""));
  if (!recording) {
    return false;
  }
  fs.writeFileSync(recording.stopFile, "stop\n", "utf8");
  recording.stopping = true;
  return true;
});

ipcMain.handle("clipboard:writeText", (_event, text) => {
  clipboard.writeText(String(text || ""));
  return true;
});

ipcMain.handle("text:encode", async (_event, payload) => {
  const args = [
    "encode",
    payload.text || "",
    "-o",
    payload.output,
    "--codec",
    payload.codec || "wechat",
    "--error-correction",
    payload.errorCorrection || "M"
  ];
  const result = await runCapture("textQr", args);
  return {
    ...result,
    imageDataUrl: result.code === 0 && fs.existsSync(payload.output) ? imageDataUrl(payload.output) : null
  };
});

ipcMain.handle("text:decode", async (_event, payload) => {
  const tmp = path.join(os.tmpdir(), `qr-text-${Date.now()}.txt`);
  const result = await runCapture("textQr", ["decode", payload.image, "-o", tmp]);
  let text = "";
  if (result.code === 0 && fs.existsSync(tmp)) {
    text = fs.readFileSync(tmp, "utf8");
    fs.unlinkSync(tmp);
  }
  return { ...result, text };
});

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on("before-quit", () => {
  for (const recording of activeRecordings.values()) {
    try {
      fs.writeFileSync(recording.stopFile, "stop\n", "utf8");
    } catch (_error) {
      // The recorder may already have finished and removed its stop file.
    }
  }
});
