const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(window.location.search);
const token = params.get("token");
const mode = params.get("mode") || "openFile";
const title = params.get("title") || "选择路径";
const filters = JSON.parse(params.get("filters") || "[]");
const initialCwd = params.get("cwd") || "";
const defaultPath = params.get("defaultPath") || "";

let cwd = initialCwd;
let parentPath = null;
let homePath = null;
let selectedPath = "";
let selectedIsDirectory = false;

function formatSize(size) {
  if (size === null || size === undefined) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function formatTime(ms) {
  if (!ms) return "";
  return new Date(ms).toLocaleString();
}

function extensionHint() {
  const extensions = filters.flatMap((filter) => filter.extensions || []).filter((ext) => ext !== "*");
  return extensions.length ? `可选类型：${extensions.map((ext) => `.${ext}`).join(" ")}` : "可选全部文件";
}

function joinPath(dir, name) {
  if (!dir) return name;
  return dir.endsWith("/") ? `${dir}${name}` : `${dir}/${name}`;
}

function setStatus(message) {
  $("status").textContent = message;
}

function selectEntry(entry, row) {
  for (const item of document.querySelectorAll("tr.selected")) item.classList.remove("selected");
  row.classList.add("selected");
  selectedPath = entry.path;
  selectedIsDirectory = entry.isDirectory;
  $("chooseButton").disabled = !canChooseSelection(entry);
  if (mode === "saveFile" && !entry.isDirectory) {
    $("nameInput").value = entry.name;
  }
}

function canChooseSelection(entry) {
  if (!entry) return false;
  if (mode === "openDirectory") return entry.isDirectory;
  if (mode === "openFile") return !entry.isDirectory && entry.selectable;
  return true;
}

async function loadDirectory(nextCwd = cwd) {
  setStatus("正在读取...");
  selectedPath = "";
  selectedIsDirectory = false;
  $("chooseButton").disabled = mode !== "saveFile" && mode !== "openDirectory";
  const result = await window.qrSuite.filePickerList(token, { cwd: nextCwd, filters, mode });
  cwd = result.cwd;
  parentPath = result.parent;
  homePath = result.home;
  $("pathInput").value = cwd;
  $("upButton").disabled = !parentPath;
  $("entries").replaceChildren();
  for (const entry of result.entries) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="name"></td>
      <td class="type"></td>
      <td class="size"></td>
      <td class="time"></td>
    `;
    row.querySelector(".name").textContent = `${entry.isDirectory ? "[DIR] " : "[FILE] "}${entry.name}`;
    row.querySelector(".type").textContent = entry.isDirectory ? "目录" : (entry.selectable || mode === "saveFile" ? "文件" : "不可选");
    row.querySelector(".size").textContent = formatSize(entry.size);
    row.querySelector(".time").textContent = formatTime(entry.mtime);
    row.addEventListener("click", () => selectEntry(entry, row));
    row.addEventListener("dblclick", async () => {
      if (entry.isDirectory) {
        await loadDirectory(entry.path);
      } else if (canChooseSelection(entry)) {
        await choose(entry.path);
      }
    });
    $("entries").append(row);
  }
  if (mode === "openDirectory") {
    $("chooseButton").disabled = false;
  }
  setStatus(`${result.entries.length} 项，${extensionHint()}`);
}

function saveTargetPath() {
  const name = $("nameInput").value.trim();
  return name ? joinPath(cwd, name) : "";
}

async function choose(pathValue = null) {
  let finalPath = pathValue;
  if (mode === "saveFile") {
    finalPath = saveTargetPath();
  } else if (mode === "openDirectory" && !finalPath) {
    finalPath = cwd;
  } else if (!finalPath) {
    finalPath = selectedPath;
  }
  if (!finalPath) {
    setStatus("请先选择路径。");
    return;
  }
  try {
    await window.qrSuite.filePickerChoose(token, { path: finalPath });
  } catch (error) {
    setStatus(error.message);
  }
}

function initializeSaveName() {
  if (mode !== "saveFile") return;
  $("saveRow").classList.remove("hidden");
  const normalized = defaultPath.replace(/\\/g, "/");
  const name = normalized.split("/").filter(Boolean).at(-1) || "output.mp4";
  $("nameInput").value = name;
  $("chooseButton").disabled = false;
}

function wireEvents() {
  $("title").textContent = title;
  document.title = title;
  $("upButton").addEventListener("click", () => {
    if (parentPath) loadDirectory(parentPath);
  });
  $("homeButton").addEventListener("click", () => {
    if (homePath) loadDirectory(homePath);
  });
  $("goButton").addEventListener("click", () => loadDirectory($("pathInput").value.trim()));
  $("pathInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadDirectory($("pathInput").value.trim());
  });
  $("nameInput").addEventListener("input", () => {
    $("chooseButton").disabled = mode === "saveFile" && !$("nameInput").value.trim();
  });
  $("chooseButton").addEventListener("click", () => choose());
  $("cancelButton").addEventListener("click", () => window.qrSuite.filePickerCancel(token));
}

wireEvents();
initializeSaveName();
loadDirectory(initialCwd).catch((error) => setStatus(error.message));
