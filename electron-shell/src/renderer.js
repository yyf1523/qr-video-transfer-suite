const $ = (id) => document.getElementById(id);
const logOutput = $("logOutput");
const runtimeBadge = $("runtimeBadge");
const WINDOWS_WORKERS = "6";
const LINUX_WORKERS = "4";
const DEFAULT_MEMORY_GB = "6";
// 加码 / 解码模式采用"数据驱动"设计：每个预设集中声明命令行参数(args)与界面展示(title/summary/values)，
// UI 通过下拉选择键名，运行时直接展开 args。新增模式只需在此追加一项，无需改动事件逻辑。
const ENCODE_MODE_PRESETS = {
  balanced: {
    title: "平衡稳定版",
    summary: "Windows 本地截图工具录屏、云桌面 1896×990 推荐。速度和成功率比较均衡。",
    args: [
      "--payload-mode", "binary",
      "--qr-error-correction", "H",
      "--qr-version", "30",
      "--box-size", "3",
      "--chunk-size", "680",
      "--fps", "30",
      "--repeat", "3",
      "--passes", "1",
      "--grid-cols", "4",
      "--grid-rows", "1",
      "--grid-gap", "10",
      "--canvas-width", "1896",
      "--canvas-height", "990",
      "--label-height", "64",
      "--label-scale", "0.45",
      "--label-thickness", "1",
      "--meta-qr-size", "48",
      "--meta-qr-version", "6",
      "--meta-qr-box-size", "2",
      "--color-border", "8",
      "--outer-white", "4",
      "--fec-group-size", "100",
      "--fec-parity-chunks", "20"
    ],
    values: [
      ["二维码布局", "4 个横排"],
      ["画布分辨率", "1896×990"],
      ["QR 版本 / 模块像素", "v30 / 3px"],
      ["分片大小", "680 字节"],
      ["帧率 / 重复", "30 FPS / 每片 3 帧"],
      ["纠错 / 冗余", "ECC-H / FEC 100+20"],
      ["适用场景", "Windows 截图工具录屏、云桌面窗口轻微缩放"]
    ]
  },
  fast: {
    title: "高速直录版",
    summary: "适合 1920×1080 全屏、无缩放、画质清晰的录制环境。速度最快，抗缩放能力较弱。",
    args: [
      "--payload-mode", "binary",
      "--qr-error-correction", "H",
      "--qr-version", "40",
      "--box-size", "2",
      "--chunk-size", "1100",
      "--fps", "30",
      "--repeat", "2",
      "--passes", "1",
      "--grid-cols", "4",
      "--grid-rows", "1",
      "--grid-gap", "10",
      "--canvas-width", "1920",
      "--canvas-height", "1080",
      "--label-height", "64",
      "--label-scale", "0.45",
      "--label-thickness", "1",
      "--meta-qr-size", "48",
      "--meta-qr-version", "6",
      "--meta-qr-box-size", "2",
      "--color-border", "8",
      "--outer-white", "4",
      "--fec-group-size", "100",
      "--fec-parity-chunks", "12"
    ],
    values: [
      ["二维码布局", "4 个横排"],
      ["画布分辨率", "1920×1080"],
      ["QR 版本 / 模块像素", "v40 / 2px"],
      ["分片大小", "1100 字节"],
      ["帧率 / 重复", "30 FPS / 每片 2 帧"],
      ["纠错 / 冗余", "ECC-H / FEC 100+12"],
      ["适用场景", "全屏无缩放直录、专用播放器全屏播放"]
    ]
  },
  window2: {
    title: "窗口稳妥版",
    summary: "适合本地竖屏、远程桌面窗口较小、录屏软件会压缩画面的情况。",
    args: [
      "--payload-mode", "binary",
      "--qr-error-correction", "H",
      "--qr-version", "30",
      "--box-size", "4",
      "--chunk-size", "680",
      "--fps", "30",
      "--repeat", "3",
      "--passes", "1",
      "--grid-cols", "2",
      "--grid-rows", "1",
      "--grid-gap", "10",
      "--canvas-width", "1280",
      "--canvas-height", "720",
      "--label-height", "64",
      "--label-scale", "0.45",
      "--label-thickness", "1",
      "--meta-qr-size", "56",
      "--meta-qr-version", "6",
      "--meta-qr-box-size", "2",
      "--color-border", "8",
      "--outer-white", "4",
      "--fec-group-size", "100",
      "--fec-parity-chunks", "24"
    ],
    values: [
      ["二维码布局", "2 个横排"],
      ["画布分辨率", "1280×720"],
      ["QR 版本 / 模块像素", "v30 / 4px"],
      ["分片大小", "680 字节"],
      ["帧率 / 重复", "30 FPS / 每片 3 帧"],
      ["纠错 / 冗余", "ECC-H / FEC 100+24"],
      ["适用场景", "竖屏电脑、远程窗口录制、画面被压小"]
    ]
  },
  safe1: {
    title: "超稳兜底版",
    summary: "给压缩严重、丢块多、反复失败的视频用。最慢，但二维码最大、容错最高。",
    args: [
      "--payload-mode", "binary",
      "--qr-error-correction", "H",
      "--qr-version", "30",
      "--box-size", "4",
      "--chunk-size", "680",
      "--fps", "30",
      "--repeat", "4",
      "--passes", "1",
      "--grid-cols", "1",
      "--grid-rows", "1",
      "--grid-gap", "10",
      "--canvas-width", "960",
      "--canvas-height", "720",
      "--label-height", "64",
      "--label-scale", "0.45",
      "--label-thickness", "1",
      "--meta-qr-size", "56",
      "--meta-qr-version", "6",
      "--meta-qr-box-size", "2",
      "--color-border", "8",
      "--outer-white", "4",
      "--fec-group-size", "100",
      "--fec-parity-chunks", "30"
    ],
    values: [
      ["二维码布局", "1 个"],
      ["画布分辨率", "960×720"],
      ["QR 版本 / 模块像素", "v30 / 4px"],
      ["分片大小", "680 字节"],
      ["帧率 / 重复", "30 FPS / 每片 4 帧"],
      ["纠错 / 冗余", "ECC-H / FEC 100+30"],
      ["适用场景", "压缩严重、窗口很小、前几种模式仍缺块"]
    ]
  }
};
const DECODE_MODE_PRESETS = {
  winRecord: {
    title: "Windows 录屏稳健",
    summary: "默认推荐。适合 Windows 截图工具录出来的 MP4，自动读取参数二维码并逐帧扫描。",
    args: ["--auto-params", "--noise-robust", "--fallback-max-side", "1920", "--progress-every", "120"],
    values: [
      ["参数读取", "自动读取 QVP1 参数二维码"],
      ["扫描方式", "由参数二维码自动决定 4QR / 2QR / 1QR"],
      ["容错增强", "开启噪声增强和 1920px 全帧回退"],
      ["进度刷新", "每 120 帧输出一次"]
    ]
  },
  auto: {
    title: "自动匹配",
    summary: "适合新版本工具生成的视频。速度较快，优先相信视频开头的参数二维码。",
    args: ["--auto-params"],
    values: [
      ["参数读取", "自动读取 QVP1 参数二维码"],
      ["扫描方式", "由参数二维码自动匹配"],
      ["适用场景", "清晰录屏、专用播放器全屏录制"]
    ]
  },
  fast4: {
    title: "旧版高速 4QR",
    summary: "兼容早期 4QR / 30FPS / FEC 视频，适合清晰且无明显缩放的录屏。",
    args: ["--screen-fast-fec-4qr", "--auto-params"],
    values: [
      ["参数读取", "自动读取，兼容旧 4QR 预设"],
      ["扫描方式", "快速全帧扫描"],
      ["适用场景", "旧版高速 4QR 视频"]
    ]
  },
  redbox: {
    title: "红框慢速救援",
    summary: "视频已经缺块很多时使用。按红色定位框逐块校正，速度慢，适合补救尝试。",
    args: [
      "--screen-grid",
      "--no-fast-screen",
      "--no-auto-params",
      "--noise-robust",
      "--max-contours", "20",
      "--min-red-area", "1000",
      "--decode-padding", "72",
      "--progress-every", "120"
    ],
    values: [
      ["参数读取", "从普通帧内识别参数，不做开头快扫"],
      ["扫描方式", "红框定位 + 透视校正"],
      ["定位数量", "每帧最多检查 20 个红框"],
      ["白边补偿", "72px"],
      ["适用场景", "录屏压缩明显、快速解码缺块严重"]
    ]
  }
};
let defaultWorkers = WINDOWS_WORKERS;
const runsById = new Map();
let activeProgressRunId = null;
let recordingRunId = null;
let recordingStartedAt = 0;
let recordingTimer = null;
let recordingMonitors = [];

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

// 按下拉框当前值取预设；下拉不存在或值非法时回退到 fallback，保证始终返回一个有效预设。
function selectedPreset(selectId, presets, fallback) {
  const selected = $(selectId)?.value || fallback;
  return presets[selected] || presets[fallback];
}

// 把预设的展示信息渲染成 标题 + 说明 + 参数键值表；容器或预设缺失时安全跳过。
function renderPresetDetail(containerId, preset) {
  const container = $(containerId);
  if (!container || !preset) return;
  const title = document.createElement("strong");
  title.textContent = preset.title;
  const summary = document.createElement("p");
  summary.textContent = preset.summary;
  const list = document.createElement("dl");
  for (const [name, value] of preset.values) {
    const term = document.createElement("dt");
    term.textContent = name;
    const detail = document.createElement("dd");
    detail.textContent = value;
    list.append(term, detail);
  }
  container.replaceChildren(title, summary, list);
}

function refreshModeDetails() {
  renderPresetDetail("encModeDetail", selectedPreset("encMode", ENCODE_MODE_PRESETS, "balanced"));
  renderPresetDetail("decModeDetail", selectedPreset("decMode", DECODE_MODE_PRESETS, "winRecord"));
}

function wireModePresets() {
  $("encMode")?.addEventListener("change", refreshModeDetails);
  $("decMode")?.addEventListener("change", refreshModeDetails);
  refreshModeDetails();
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

function ensureExtension(filePath, extension) {
  const trimmed = String(filePath || "").trim();
  if (!trimmed) return "";
  return trimmed.toLowerCase().endsWith(extension.toLowerCase()) ? trimmed : `${trimmed}${extension}`;
}

// 拆分路径为 { 目录, 文件名, 分隔符 }，兼容 Windows "\\" 与 POSIX "/"。
// 没有分隔符时（纯文件名）返回 { dir: "", base }，此时不含 sep —— joinPath 会因 dir 为空而忽略分隔符。
function splitPath(filePath) {
  const value = String(filePath || "").trim();
  const index = Math.max(value.lastIndexOf("\\"), value.lastIndexOf("/"));
  if (index < 0) return { dir: "", base: value };
  return { dir: value.slice(0, index), base: value.slice(index + 1), sep: value[index] };
}

// 用原始分隔符拼回路径；dir 为空时直接返回文件名，避免出现前导分隔符。
function joinPath(dir, base, sep = "\\") {
  return dir ? `${dir}${sep}${base}` : base;
}

// 已知扩展名表。多段扩展名（.tar.gz）必须排在单段（.gz）之前，
// 否则 "a.tar.gz" 会先命中 .gz 而只剥掉一段。
const ARCHIVE_EXTENSIONS = [".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".zip", ".7z", ".tgz", ".tar", ".gz"];
const VIDEO_EXTENSIONS = [".mp4", ".avi", ".mov", ".mkv", ".webm"];

// 去掉文件名末尾的已知扩展名：命中 suffixes 中的（可能多段的）扩展名时整体剥离，
// 否则回退到最后一个 "." 之前；index > 0 的判断保留形如 ".gitignore" 这类以点开头的名字。
function stripSuffix(fileName, suffixes) {
  const lower = fileName.toLowerCase();
  for (const suffix of suffixes) {
    if (lower.endsWith(suffix)) return fileName.slice(0, -suffix.length);
  }
  const index = fileName.lastIndexOf(".");
  return index > 0 ? fileName.slice(0, index) : fileName;
}

// 压缩包路径 → 同目录、同名的 .mp4 输出路径（沿用原始路径分隔符）。
function defaultEncodedVideoPathForArchive(archivePath) {
  const { dir, base, sep } = splitPath(archivePath);
  const name = stripSuffix(base, ARCHIVE_EXTENSIONS);
  return joinPath(dir, `${name}.mp4`, sep);
}

// 视频路径 → 同目录、以视频名为目录名的解码输出目录。
function defaultDecodeOutputDirForVideo(videoPath) {
  const { dir, base, sep } = splitPath(videoPath);
  return joinPath(dir, stripSuffix(base, VIDEO_EXTENSIONS), sep);
}

// 是否应把输出路径替换为"依据输入自动推导的默认值"：
// 仅当用户提供了输入、且当前输出为空或仍是初始占位默认值时才替换，
// 从而不会覆盖用户手动填写的输出路径。
function shouldUseDerivedOutput(input, currentOutput, placeholder) {
  return Boolean(input) && (!currentOutput || currentOutput === placeholder);
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
          value = await window.qrSuite.saveFile({ defaultPath: ensureExtension(target.value || "hd_secure_stream", ".mp4"), filters: [{ name: "MP4", extensions: ["mp4"] }] });
          if (value) value = ensureExtension(value, ".mp4");
        } else if (kind === "saveRecording") {
          value = await window.qrSuite.saveFile({ defaultPath: target.value || defaultRecordingPath(), filters: [{ name: "MP4", extensions: ["mp4"] }] });
          if (value) value = ensureExtension(value, ".mp4");
        } else if (kind === "savePng") {
          value = await window.qrSuite.saveFile({ defaultPath: target.value || "text-qr.png", filters: [{ name: "PNG", extensions: ["png"] }] });
          if (value) value = ensureExtension(value, ".png");
        } else if (kind === "video") {
          value = await window.qrSuite.openFile({ title: "选择视频文件", filters: [{ name: "视频", extensions: ["mp4", "avi", "mov", "mkv", "webm"] }] });
        } else if (kind === "manifest") {
          value = await window.qrSuite.openFile({ title: "选择 manifest.json", filters: [{ name: "JSON", extensions: ["json"] }] });
        } else if (kind === "image") {
          value = await window.qrSuite.openFile({ title: "选择二维码图片", filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "bmp", "webp"] }] });
        } else {
          value = await window.qrSuite.openFile();
        }
        if (value) {
          target.value = value;
          if (target.id === "encSource") {
            $("encOutput").value = defaultEncodedVideoPathForArchive(value);
            log(`输出视频默认使用压缩包同名：${$("encOutput").value}`);
          }
          if (target.id === "decVideo") {
            $("decOutput").value = defaultDecodeOutputDirForVideo(value);
            log(`解码输出目录默认使用视频同名：${$("decOutput").value}`);
          }
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
    recordingMonitors = monitors;
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
    recordingMonitors = [];
    log(`读取屏幕失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "刷新屏幕";
  }
}

// 取当前选中的录制显示器：优先匹配下拉框选的 index，其次退回 1 号屏，再退回第一个，最后 null。
// 返回的 monitor 字段来自 Python 端 mss 枚举（index/label/left/top/width/height，坐标为物理像素）。
function selectedRecordingMonitor() {
  const selected = Number($("recordMonitor").value || 1);
  return recordingMonitors.find((monitor) => Number(monitor.index) === selected)
    || recordingMonitors.find((monitor) => Number(monitor.index) === 1)
    || recordingMonitors[0]
    || null;
}

// 把遮罩层里框选的矩形换算成 ffmpeg/gdigrab 需要的 "left,top,width,height" 物理像素区域。
// selection 的坐标是遮罩窗口内的 CSS 像素(DIP)，viewportWidth/Height 为遮罩窗口尺寸(DIP)；
// monitor.* 是 mss 报告的物理像素。因此用 物理像素/DIP 的比例做缩放，再叠加显示器物理原点偏移。
// 分母用 Math.max(1, ...) 防止除零；宽高至少为 1 像素，避免生成非法的 0 尺寸区域。
// 注意：此处假设 renderer 的 mss monitor.index 与 main 进程按 Electron 排序选中的显示器指向同一块屏幕，
//       在非常规多屏布局下二者顺序可能不一致（详见 main.cjs displayForRecordingMonitor 注释）。
function mapSelectionToMonitorRegion(selection, monitor) {
  const widthRatio = monitor.width / Math.max(1, selection.viewportWidth);
  const heightRatio = monitor.height / Math.max(1, selection.viewportHeight);
  const left = Math.round(monitor.left + selection.x * widthRatio);
  const top = Math.round(monitor.top + selection.y * heightRatio);
  const width = Math.max(1, Math.round(selection.width * widthRatio));
  const height = Math.max(1, Math.round(selection.height * heightRatio));
  return `${left},${top},${width},${height}`;
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

  $("selectRecordRegion").addEventListener("click", async () => {
    if (recordingRunId) {
      $("recordStatusText").textContent = "录制中不能重新框选区域。";
      return;
    }
    const monitor = selectedRecordingMonitor();
    if (!monitor || Number(monitor.index) === 0) {
      $("recordStatusText").textContent = "请先选择一个具体屏幕，再框选录制区域。";
      setRecordingIndicator("error", "请选择屏幕");
      return;
    }
    const button = $("selectRecordRegion");
    button.disabled = true;
    $("recordStatusText").textContent = "请在弹出的遮罩层中拖拽选择录制区域。";
    setRecordingIndicator("idle", "正在框选");
    try {
      const selection = await window.qrSuite.selectRecordingRegion({ monitor: Number(monitor.index) });
      if (!selection) {
        $("recordStatusText").textContent = "已取消框选。";
        setRecordingIndicator("idle", "已就绪");
        return;
      }
      const region = mapSelectionToMonitorRegion(selection, monitor);
      $("recordRegion").value = region;
      $("recordStatusText").textContent = `已选择录制区域：${region}`;
      setRecordingIndicator("idle", "区域已选");
      log(`已框选录制区域：${region}`);
    } catch (error) {
      $("recordStatusText").textContent = `框选失败：${error.message}`;
      setRecordingIndicator("error", "框选失败");
      log(`框选录制区域失败：${error.message}`);
    } finally {
      button.disabled = false;
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
      const selectedFps = document.querySelector('input[name="recordFps"]:checked')?.value || "60";
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
    const source = $("encSource").value.trim();
    if (source) args.push("--source", source);
    const currentOutput = $("encOutput").value.trim();
    const shouldUseSourceName = shouldUseDerivedOutput(source, currentOutput, "hd_secure_stream.mp4");
    const rawOutput = shouldUseSourceName ? defaultEncodedVideoPathForArchive(source) : (currentOutput || "hd_secure_stream.mp4");
    const output = ensureExtension(rawOutput, ".mp4");
    if (output !== rawOutput) {
      $("encOutput").value = output;
      log(`输出路径已自动改为 MP4：${output}`);
    } else if (!$("encOutput").value.trim() || shouldUseSourceName) {
      $("encOutput").value = output;
    }
    args.push("-o", output);
    args.push(...selectedPreset("encMode", ENCODE_MODE_PRESETS, "balanced").args);
    if ($("encPipeOutput").checked) args.push("--mp4-profile", "uos-pipe");
    if ($("encHardwareH264").checked) args.push("--h264-encoder", "h264_vaapi");
    appendPerformanceDefaults(args, $("encExtra").value);
    await runStreamingTask(encodeButton, "videoEncode", args);
  });

  const decodeButton = $("runDecode");
  decodeButton.dataset.label = decodeButton.textContent;
  decodeButton.addEventListener("click", async () => {
    const args = [];
    const video = $("decVideo").value.trim();
    if (video) args.push(video);
    args.push(...selectedPreset("decMode", DECODE_MODE_PRESETS, "winRecord").args);
    args.push("--name-single-source-from-video");
    if ($("decManifest").value.trim()) args.push("--manifest-json", $("decManifest").value.trim());
    args.push("--state-dir", defaultDecodeStatePath());
    const currentDecodeOutput = $("decOutput").value.trim();
    const shouldUseVideoName = shouldUseDerivedOutput(video, currentDecodeOutput, "reconstructed_project");
    const outputDir = shouldUseVideoName ? defaultDecodeOutputDirForVideo(video) : (currentDecodeOutput || "reconstructed_project");
    if (!currentDecodeOutput || shouldUseVideoName) $("decOutput").value = outputDir;
    args.push("-o", outputDir);
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
  wireModePresets();
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
