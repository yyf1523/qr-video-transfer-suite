// 全屏遮罩层框选脚本：主进程用 ?token=... 打开本页面，用户拖拽出矩形后，
// 通过按 token 命名的 IPC 频道把结果回传给主进程（见 main.cjs showRegionSelector）。
const params = new URLSearchParams(window.location.search);
const token = params.get("token") || "";
const selection = document.getElementById("selection");
const selectionSize = document.getElementById("selectionSize");

// 最小有效边长（DIP）。小于该值视为误触/单击，按取消处理，避免生成无意义的极小录制区。
const MIN_SELECTION_SIZE = 12;

let dragging = false;
let startX = 0;
let startY = 0;
let currentRect = null;

// 取消框选：仅在拿到有效 token 时才调用主进程频道，
// 否则该频道尚未注册，invoke 会抛错并产生未捕获的 Promise 拒绝。
async function cancelSelection() {
  if (!token) return;
  await window.qrSuite.regionSelectorCancel(token);
}

// 把两点规整为落在视口内的矩形 {x, y, width, height}（左上原点，非负宽高）。
function normalizedRect(x1, y1, x2, y2) {
  const left = Math.max(0, Math.min(x1, x2));
  const top = Math.max(0, Math.min(y1, y2));
  const right = Math.min(window.innerWidth, Math.max(x1, x2));
  const bottom = Math.min(window.innerHeight, Math.max(y1, y2));
  return {
    x: left,
    y: top,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top)
  };
}

function drawRect(rect) {
  currentRect = rect;
  selection.hidden = rect.width < 1 || rect.height < 1;
  selection.style.left = `${rect.x}px`;
  selection.style.top = `${rect.y}px`;
  selection.style.width = `${rect.width}px`;
  selection.style.height = `${rect.height}px`;
  selectionSize.textContent = `${Math.round(rect.width)}×${Math.round(rect.height)}`;
}

// 确认框选：无效（无 token / 无矩形 / 尺寸过小）时按取消处理；
// 有效时连同当前遮罩窗口的视口尺寸一并回传，供主进程/渲染进程换算物理像素区域。
async function finish(rect) {
  if (!token || !rect || rect.width < MIN_SELECTION_SIZE || rect.height < MIN_SELECTION_SIZE) {
    await cancelSelection();
    return;
  }
  await window.qrSuite.regionSelectorFinish(token, {
    ...rect,
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight
  });
}

window.addEventListener("mousedown", (event) => {
  if (event.button !== 0) return;
  dragging = true;
  startX = event.clientX;
  startY = event.clientY;
  drawRect(normalizedRect(startX, startY, startX, startY));
});

window.addEventListener("mousemove", (event) => {
  if (!dragging) return;
  drawRect(normalizedRect(startX, startY, event.clientX, event.clientY));
});

window.addEventListener("mouseup", async (event) => {
  if (!dragging || event.button !== 0) return;
  dragging = false;
  const rect = normalizedRect(startX, startY, event.clientX, event.clientY);
  drawRect(rect);
  await finish(rect);
});

// 键盘快捷键：Esc 取消，Enter 确认当前已画出的矩形。
window.addEventListener("keydown", async (event) => {
  if (event.key === "Escape") {
    await cancelSelection();
  }
  if (event.key === "Enter" && currentRect) {
    await finish(currentRect);
  }
});
