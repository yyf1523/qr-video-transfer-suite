const $ = (id) => document.getElementById(id);
const logOutput = $("logOutput");
const runtimeBadge = $("runtimeBadge");
const WINDOWS_WORKERS = "6";
const LINUX_WORKERS = "4";
const DEFAULT_MEMORY_GB = "6";
let defaultWorkers = WINDOWS_WORKERS;
const runsById = new Map();
let activeProgressRunId = null;
let recordingRunId = null;
let recordingStartedAt = 0;
let recordingTimer = null;

function toolTitle(toolId) {
  if (toolId === "videoEncode") return "视频加码";
  if (toolId === "videoDecode") return "视频解码";
  if (toolId === "screenRecord") return "屏幕录制";
  return "任务";
}

function log(message) {
  const current = logOutput.textContent === "等待操作..." ? "" : `${logOutput.textContent}\n`;
  const now = new Date().toLocaleTimeString();
  logOutput.textContent = `${current}[${now}] ${message}`;
  logOutput.scrollTop = logOutput.scrollHeight;
}

function appLog(level, message, data = null) {
  try {
    const result = window.qrSuite.writeAppLog({ level, message, data });
    if (result && typeof result.catch === "function") {
      result.catch(() => {});
    }
  } catch (_error) {
    // Main-process logging is best-effort from the renderer.
  }
}

window.addEventListener("error", (event) => {
  appLog("renderer-error", "window.error", {
    message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
    stack: event.error?.stack
  });
});

window.addEventListener("unhandledrejection", (event) => {
  appLog("renderer-error", "unhandledrejection", {
    reason: event.reason?.message || String(event.reason),
    stack: event.reason?.stack
  });
});

function splitArgs(raw) {
  const args = [];
  const pattern = /"([^"]*)"|'([^']*)'|[^\s]+/g;
  let match;
  while ((match = pattern.exec(raw || ""))) {
    args.push(match[1] ?? match[2] ?? match[0]);
  }
  return args;
}

function hasOption(args, optionName) {
  return args.some((arg) => arg === optionName || arg.startsWith(`${optionName}=`));
}

function appendPerformanceDefaults(args, rawExtra) {
  const extraArgs = splitArgs(rawExtra);
  if (!hasOption(extraArgs, "--workers")) {
    args.push("--workers", defaultWorkers);
  }
  if (!hasOption(extraArgs, "--memory-gb")) {
    args.push("--memory-gb", DEFAULT_MEMORY_GB);
  }
  args.push(...extraArgs);
}

function applyPlatformDefaults(info) {
  defaultWorkers = info.platform === "linux" ? LINUX_WORKERS : WINDOWS_WORKERS;
  const defaultExtra = `--workers ${defaultWorkers} --memory-gb ${DEFAULT_MEMORY_GB}`;
  const performanceValue = $("performanceValue");

  if (performanceValue) {
    performanceValue.textContent = `${DEFAULT_MEMORY_GB}GB / ${defaultWorkers} 并发`;
  }

  for (const input of [$("encExtra"), $("decExtra")]) {
    if (input && /^--workers\s+\d+\s+--memory-gb\s+\d+(\.\d+)?$/.test(input.value.trim())) {
      input.value = defaultExtra;
    }
  }
}

function createProgressState(toolId) {
  return {
    toolId,
    buttonId: null,
    done: 0,
    total: 0,
    totalsByFile: new Map(),
    seenByFile: new Map()
  };
}

function progressState(runId) {
  return runsById.get(runId) || runsById.get(activeProgressRunId);
}

function setProgressView(state, detail = "") {
  const total = Math.max(0, Number(state.total) || 0);
  const done = total > 0 ? Math.min(total, Math.max(0, Number(state.done) || 0)) : 0;
  const percent = total > 0 ? Math.round((done / total) * 100) : 0;
  const title = `${toolTitle(state.toolId)}进度`;

  $("progressCard").classList.toggle("active", total > 0 || done > 0);
  $("progressTitle").textContent = title;
  $("progressDetail").textContent = detail || (total > 0 ? "正在按分片处理..." : "正在扫描参数与分片...");
  $("progressPercent").textContent = total > 0 ? `${percent}%` : "0%";
  $("progressFill").style.width = `${percent}%`;
  $("progressMeta").textContent = total > 0 ? `${done} / ${total} 分片` : "等待总分片数";
}

function resetProgress(toolId = null) {
  const state = createProgressState(toolId);
  $("progressCard").classList.remove("active");
  $("progressTitle").textContent = toolId ? `${toolTitle(toolId)}进度` : "等待任务";
  $("progressDetail").textContent = toolId ? "正在启动任务..." : "启动加码或解码后按分片实时刷新。";
  $("progressPercent").textContent = "0%";
  $("progressFill").style.width = "0%";
  $("progressMeta").textContent = "0 / 0 分片";
  return state;
}

function setStructuredProgress(state, done, total, detail) {
  state.done = Math.max(0, Number(done) || 0);
  state.total = Math.max(0, Number(total) || 0);
  setProgressView(state, detail);
}

function updateDecodeProgressFromChunk(state, fileName, index, total, detail) {
  if (!state.totalsByFile.has(fileName)) {
    state.totalsByFile.set(fileName, total);
  }
  if (!state.seenByFile.has(fileName)) {
    state.seenByFile.set(fileName, new Set());
  }
  state.seenByFile.get(fileName).add(index);
  state.total = Array.from(state.totalsByFile.values()).reduce((sum, value) => sum + value, 0);
  state.done = Array.from(state.seenByFile.values()).reduce((sum, seen) => sum + seen.size, 0);
  setProgressView(state, detail);
}

function updateProgressFromLog(line, runId) {
  const state = progressState(runId);
  if (!state) return;

  const structured = line.match(/^\[PROGRESS\]\s+(encode|decode)\s+(\d+)\/(\d+)\s+chunks(?:\s+[\d.]+%)?(?:\s+(.*))?$/i);
  if (structured) {
    setStructuredProgress(state, Number(structured[2]), Number(structured[3]), structured[4] || "");
    return;
  }

  const decodedChunk = line.match(/^\[(?:OK|FEC-OK)\]\s+(.+)\s+chunk\s+(\d+)\/(\d+)/);
  if (decodedChunk) {
    const fileName = decodedChunk[1].trim();
    const index = Number(decodedChunk[2]);
    const total = Number(decodedChunk[3]);
    updateDecodeProgressFromChunk(state, fileName, index, total, `${fileName} ${index}/${total}`);
    return;
  }

  const frameScan = line.match(/^\[INFO\]\s+(?:Fast\s+)?(?:scan complete\.|frames=)/i);
  if (frameScan && state.toolId === "videoDecode" && state.total === 0) {
    setProgressView(state, "正在扫描视频帧，等待读取参数二维码或有效分片...");
  }
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.textContent = busy ? "运行中..." : button.dataset.label;
}

function wireWindowControls() {
  for (const button of document.querySelectorAll(".window-control")) {
    button.addEventListener("click", async () => {
      try {
        await window.qrSuite.controlWindow(button.dataset.windowAction);
      } catch (error) {
        log(`窗口控制失败：${error.message}`);
      }
    });
  }
}

async function runStreamingTask(button, toolId, args) {
  setBusy(button, true);
  const runId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const state = createProgressState(toolId);
  state.buttonId = button.id;
  state.args = args;
  runsById.set(runId, state);
  activeProgressRunId = runId;
  resetProgress(toolId);
  setProgressView(state, "任务已在后台启动，可继续使用其他功能。");
  try {
    const task = await window.qrSuite.runTask({ runId, toolId, args });
    setProgressView(state, "任务正在后台运行，可切换页签或执行其他工具。");
    log(`启动 ${toolId}: ${task.command} ${task.args.join(" ")}`);
  } catch (error) {
    runsById.delete(runId);
    if (activeProgressRunId === runId) activeProgressRunId = null;
    log(`启动失败：${error.message}`);
    setBusy(button, false);
    resetProgress();
  }
}

function timestampForPath() {
  const now = new Date();
  const pad = (value) => String(value).padStart(2, "0");
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

function activateTab(tabName) {
  const tab = document.querySelector(`.tab[data-tab="${tabName}"]`);
  const panel = $(`panel-${tabName}`);
  if (!tab || !panel) return;
  for (const item of document.querySelectorAll(".tab")) item.classList.remove("active");
  for (const item of document.querySelectorAll(".panel")) item.classList.remove("active");
  tab.classList.add("active");
  panel.classList.add("active");
}

function wireTabs() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
  }
}

function wirePickers() {
  for (const button of document.querySelectorAll(".pick")) {
    button.addEventListener("click", async () => {
      const target = $(button.dataset.pick);
      const kind = button.dataset.kind;
      let value = null;
      try {
        log(`打开选择器：${button.textContent.trim() || kind || "文件"}`);
        if (kind === "dir") {
          value = await window.qrSuite.openDirectory();
        } else if (kind === "archive") {
          value = await window.qrSuite.openFile({
            title: "选择源码压缩包",
            filters: [{ name: "压缩包", extensions: ["zip", "7z", "tar", "gz", "tgz"] }]
          });
        } else if (kind === "saveVideo") {
          value = await window.qrSuite.saveFile({ defaultPath: target.value || "hd_secure_stream.mp4", filters: [{ name: "MP4", extensions: ["mp4"] }] });
        } else if (kind === "saveRecording") {
          value = await window.qrSuite.saveFile({ defaultPath: target.value || defaultRecordingPath(), filters: [{ name: "MP4", extensions: ["mp4"] }] });
        } else if (kind === "savePng") {
          value = await window.qrSuite.saveFile({ defaultPath: target.value || "text-qr.png", filters: [{ name: "PNG", extensions: ["png"] }] });
        } else if (kind === "video") {
          value = await window.qrSuite.openFile({ title: "选择视频文件", filters: [{ name: "视频", extensions: ["mp4", "avi", "mov", "mkv", "webm"] }] });
        } else {
          value = await window.qrSuite.openFile();
        }
        if (value) {
          target.value = value;
          log(`已选择：${value}`);
        }
      } catch (error) {
        log(`选择失败：${error.message}`);
        appLog("renderer-error", "Picker failed", { kind, error: error.message });
      }
    });
  }
}

function defaultRecordingPath() {
  return `screen_recordings/screen-record-${timestampForPath()}.mp4`;
}

function defaultDecodeStatePath() {
  return `decode_state/session-${timestampForPath()}`;
}

function formatElapsed(milliseconds) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function setRecordingIndicator(state, label) {
  const indicator = $("recordIndicator");
  indicator.classList.toggle("recording", state === "recording");
  indicator.classList.toggle("saving", state === "saving");
  indicator.classList.toggle("error", state === "error");
  indicator.querySelector("span").textContent = label;
}

function validateRecordRegion(rawRegion) {
  const value = rawRegion.trim();
  if (!value) return "";
  const parts = value.split(",").map((part) => part.trim());
  if (parts.length !== 4 || parts.some((part) => !/^-?\d+$/.test(part))) {
    throw new Error("录制区域必须为 left,top,width,height 四个整数。");
  }
  if (Number(parts[2]) < 1 || Number(parts[3]) < 1) {
    throw new Error("录制区域的宽度和高度必须大于 0。");
  }
  return parts.join(",");
}

async function refreshRecordingMonitors() {
  const button = $("refreshMonitors");
  const select = $("recordMonitor");
  const previous = select.value;
  button.disabled = true;
  button.textContent = "读取中...";
  try {
    const monitors = await window.qrSuite.listRecordingMonitors();
    select.replaceChildren();
    for (const monitor of monitors) {
      const option = document.createElement("option");
      option.value = String(monitor.index);
      const scope = monitor.index === 0 ? "全部屏幕" : `屏幕 ${monitor.index}`;
      option.textContent = `${scope} · ${monitor.width}×${monitor.height} · ${monitor.left},${monitor.top}`;
      select.append(option);
    }
    if (Array.from(select.options).some((option) => option.value === previous)) select.value = previous;
    else if (select.options.length > 1) select.value = "1";
    log(`已读取 ${monitors.length} 个录制范围。`);
  } catch (error) {
    log(`读取屏幕失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "刷新屏幕";
  }
}

function startRecordingClock() {
  window.clearInterval(recordingTimer);
  recordingStartedAt = Date.now();
  $("recordElapsed").textContent = "00:00";
  recordingTimer = window.setInterval(() => {
    $("recordElapsed").textContent = formatElapsed(Date.now() - recordingStartedAt);
  }, 250);
}

function finishRecording(state, payload) {
  window.clearInterval(recordingTimer);
  recordingTimer = null;
  recordingRunId = null;
  const startButton = $("startRecording");
  startButton.disabled = false;
  startButton.textContent = startButton.dataset.label;
  $("stopRecording").disabled = true;

  if (payload.code === 0) {
    setRecordingIndicator("idle", "已保存");
    $("recordStatusText").textContent = `已保存：${state.output}`;
    $("decVideo").value = state.output;
    log(`录屏完成：${state.output}`);
    if (state.autoDecode) {
      activateTab("decode");
      window.setTimeout(() => $("runDecode").click(), 180);
    }
  } else {
    setRecordingIndicator("error", "录制失败");
    $("recordStatusText").textContent = payload.error || `录制任务退出，代码 ${payload.code}`;
  }
}

function wireMediaTasks() {
  const openPlayerButton = $("openPlayer");
  openPlayerButton.dataset.label = openPlayerButton.textContent;
  openPlayerButton.addEventListener("click", async () => {
    const filePath = $("playerVideo").value.trim();
    if (!filePath) {
      $("playerStatus").textContent = "请先选择视频文件。";
      return;
    }
    setBusy(openPlayerButton, true);
    try {
      await window.qrSuite.openPlayer({
        filePath,
        autoplay: $("playerAutoplay").checked,
        loop: $("playerLoop").checked,
        fullscreen: $("playerFullscreen").checked
      });
      $("playerStatus").textContent = "专用播放器已打开。";
    } catch (error) {
      $("playerStatus").textContent = `打开失败：${error.message}`;
      log(`播放器打开失败：${error.message}`);
    } finally {
      setBusy(openPlayerButton, false);
    }
  });

  $("openSystemPlayer").addEventListener("click", async () => {
    try {
      await window.qrSuite.openSystemPlayer($("playerVideo").value.trim());
      $("playerStatus").textContent = "已交给系统播放器。";
    } catch (error) {
      $("playerStatus").textContent = `系统播放器打开失败：${error.message}`;
    }
  });

  const startButton = $("startRecording");
  startButton.dataset.label = startButton.textContent;
  startButton.addEventListener("click", async () => {
    if (recordingRunId) return;
    let region;
    try {
      region = validateRecordRegion($("recordRegion").value);
    } catch (error) {
      $("recordStatusText").textContent = error.message;
      setRecordingIndicator("error", "参数错误");
      return;
    }

    const countdown = Number($("recordCountdown").value || 0);
    startButton.disabled = true;
    for (let remaining = countdown; remaining > 0; remaining -= 1) {
      startButton.textContent = `${remaining} 秒后录制`;
      $("recordStatusText").textContent = `录制将在 ${remaining} 秒后开始。`;
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
    }

    const runId = `record-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const output = $("recordOutput").value.trim() || defaultRecordingPath();
    const state = createProgressState("screenRecord");
    state.kind = "recording";
    state.output = output;
    state.autoDecode = $("recordAutoDecode").checked;
    runsById.set(runId, state);

    try {
      const selectedFps = document.querySelector('input[name="recordFps"]:checked')?.value || "30";
      const task = await window.qrSuite.startRecording({
        runId,
        output,
        fps: Number(selectedFps),
        monitor: Number($("recordMonitor").value || 1),
        region
      });
      state.output = task.output;
      recordingRunId = runId;
      $("recordOutput").value = task.output;
      startButton.textContent = "录制中...";
      $("stopRecording").disabled = false;
      $("recordStatusText").textContent = `正在录制：${task.output}`;
      setRecordingIndicator("recording", "录制中");
      startRecordingClock();
      log(`开始屏幕录制：${task.output}`);
    } catch (error) {
      runsById.delete(runId);
      startButton.disabled = false;
      startButton.textContent = startButton.dataset.label;
      $("recordStatusText").textContent = `录制启动失败：${error.message}`;
      setRecordingIndicator("error", "启动失败");
      log(`录制启动失败：${error.message}`);
    }
  });

  $("stopRecording").addEventListener("click", async () => {
    if (!recordingRunId) return;
    $("stopRecording").disabled = true;
    $("recordStatusText").textContent = "正在写入视频尾部并保存...";
    setRecordingIndicator("saving", "正在保存");
    try {
      const accepted = await window.qrSuite.stopRecording(recordingRunId);
      if (!accepted) throw new Error("录屏任务已结束。");
    } catch (error) {
      $("recordStatusText").textContent = `停止失败：${error.message}`;
      $("stopRecording").disabled = false;
      setRecordingIndicator("error", "停止失败");
    }
  });

  $("refreshMonitors").addEventListener("click", refreshRecordingMonitors);
}

function outputArgValue(args) {
  for (let index = 0; index < args.length - 1; index += 1) {
    if (args[index] === "-o" || args[index] === "--output") {
      return args[index + 1];
    }
  }
  const inline = args.find((arg) => arg.startsWith("--output="));
  return inline ? inline.slice("--output=".length) : null;
}

function wireVideoTasks() {
  const encodeButton = $("runEncode");
  encodeButton.dataset.label = encodeButton.textContent;
  encodeButton.addEventListener("click", async () => {
    const args = [];
    if ($("encSource").value.trim()) args.push("--source", $("encSource").value.trim());
    const output = $("encOutput").value.trim() || "hd_secure_stream.mp4";
    args.push("-o", output);
    if ($("encFastFec").checked) args.push("--fast-fec-4qr");
    appendPerformanceDefaults(args, $("encExtra").value);
    await runStreamingTask(encodeButton, "videoEncode", args);
  });

  const decodeButton = $("runDecode");
  decodeButton.dataset.label = decodeButton.textContent;
  decodeButton.addEventListener("click", async () => {
    const args = [];
    if ($("decVideo").value.trim()) args.push($("decVideo").value.trim());
    if ($("decFastFec").checked) args.push("--screen-fast-fec-4qr");
    if ($("decManifest").value.trim()) args.push("--manifest-json", $("decManifest").value.trim());
    args.push("--state-dir", defaultDecodeStatePath());
    args.push("-o", $("decOutput").value.trim() || "reconstructed_project");
    appendPerformanceDefaults(args, $("decExtra").value);
    await runStreamingTask(decodeButton, "videoDecode", args);
  });
}

function wireTextTasks() {
  const encodeButton = $("runTextEncode");
  encodeButton.dataset.label = encodeButton.textContent;
  encodeButton.addEventListener("click", async () => {
    setBusy(encodeButton, true);
    try {
      const result = await window.qrSuite.encodeTextQr({
        text: $("textInput").value,
        output: $("textOutput").value.trim() || "text-qr.png",
        codec: $("textCodec").value,
        errorCorrection: $("textEcc").value
      });
      log(result.stdout.trim() || "文本二维码已生成。");
      if (result.stderr.trim()) log(result.stderr.trim());
      if (result.imageDataUrl) $("qrPreview").src = result.imageDataUrl;
      if (result.code !== 0) log(`文本二维码生成失败，退出码 ${result.code}`);
    } catch (error) {
      log(`文本二维码生成失败：${error.message}`);
    } finally {
      setBusy(encodeButton, false);
    }
  });

  const decodeButton = $("runTextDecode");
  decodeButton.dataset.label = decodeButton.textContent;
  decodeButton.addEventListener("click", async () => {
    setBusy(decodeButton, true);
    try {
      const result = await window.qrSuite.decodeTextQr({ image: $("textImage").value.trim() });
      if (result.stdout.trim()) log(result.stdout.trim());
      if (result.stderr.trim()) log(result.stderr.trim());
      $("textDecoded").value = result.text || "";
      if (result.code !== 0) log(`文本二维码解码失败，退出码 ${result.code}`);
    } catch (error) {
      log(`文本二维码解码失败：${error.message}`);
    } finally {
      setBusy(decodeButton, false);
    }
  });
}

function wireTaskEvents() {
  window.qrSuite.onTaskLog((payload) => {
    if (runsById.has(payload.runId)) {
      activeProgressRunId = payload.runId;
    }
    for (const line of payload.text.split(/\r?\n/).filter(Boolean)) {
      updateProgressFromLog(line, payload.runId);
      log(line);
    }
  });
  window.qrSuite.onTaskDone((payload) => {
    const state = runsById.get(payload.runId) || progressState(payload.runId);
    if (state?.kind === "recording") {
      log(payload.code === 0 ? "录屏任务完成。" : `录屏任务结束，退出码 ${payload.code}${payload.error ? `：${payload.error}` : ""}`);
      finishRecording(state, payload);
      runsById.delete(payload.runId);
      return;
    }
    if (state?.toolId === "videoEncode" && payload.code === 0) {
      const output = outputArgValue(state.args || []);
      if (output) {
        $("playerVideo").value = output;
        $("playerStatus").textContent = "加码视频已生成，可直接打开专用播放器。";
      }
    }
    if (state && payload.code === 0 && state.total > 0) {
      state.done = state.total;
      setProgressView(state, "任务完成。");
    } else if (state && payload.code !== 0) {
      setProgressView(state, `任务结束，退出码 ${payload.code}`);
    }
    log(payload.code === 0 ? "任务完成。" : `任务结束，退出码 ${payload.code}${payload.error ? `：${payload.error}` : ""}`);
    const completedButton = state?.buttonId ? $(state.buttonId) : null;
    if (completedButton) {
      setBusy(completedButton, false);
    }
    if (payload.runId) runsById.delete(payload.runId);
    if (activeProgressRunId === payload.runId) {
      const remaining = Array.from(runsById.entries()).at(-1);
      activeProgressRunId = remaining ? remaining[0] : null;
      if (remaining) {
        setProgressView(remaining[1], "后台任务仍在运行。");
      }
    }
  });
}

async function boot() {
  const info = await window.qrSuite.getInfo();
  applyPlatformDefaults(info);
  wireWindowControls();
  wireTabs();
  wirePickers();
  wireVideoTasks();
  wireMediaTasks();
  wireTextTasks();
  wireTaskEvents();
  $("recordOutput").value = defaultRecordingPath();
  refreshRecordingMonitors();
  $("copyLog").addEventListener("click", async () => {
    await window.qrSuite.copyText(logOutput.textContent);
    const button = $("copyLog");
    const original = button.textContent;
    button.textContent = "已复制";
    window.setTimeout(() => {
      button.textContent = original;
    }, 1200);
  });
  $("openLogs").addEventListener("click", async () => {
    try {
      const dir = await window.qrSuite.openLogsDirectory();
      log(`已打开日志目录：${dir}`);
    } catch (error) {
      log(`打开日志目录失败：${error.message}`);
    }
  });
  $("clearLog").addEventListener("click", () => {
    logOutput.textContent = "等待操作...";
    resetProgress();
  });
  runtimeBadge.textContent = `${info.platform} / Electron ${info.electron}${info.packaged ? " / packaged" : " / dev"}`;
  log("Electron GUI 初始化完成。");
  if (info.logFile) {
    log(`日志文件：${info.logFile}`);
  }
}

boot().catch((error) => log(`初始化失败：${error.message}`));
