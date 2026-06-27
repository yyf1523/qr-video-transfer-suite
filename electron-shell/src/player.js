const params = new URLSearchParams(window.location.search);
const video = document.getElementById("video");
const shell = document.getElementById("playerShell");
const mediaTitle = document.getElementById("mediaTitle");
const playbackStatus = document.getElementById("playbackStatus");
const statusDot = document.getElementById("statusDot");
const togglePlayback = document.getElementById("togglePlayback");
const toggleMute = document.getElementById("toggleMute");
const toggleLoop = document.getElementById("toggleLoop");
const seekBar = document.getElementById("seekBar");
const currentTime = document.getElementById("currentTime");
const duration = document.getElementById("duration");
const playerError = document.getElementById("playerError");
const playerErrorDetail = document.getElementById("playerErrorDetail");
const shouldAutoplay = params.get("autoplay") !== "0";
let hideTimer = null;

function formatTime(value) {
  if (!Number.isFinite(value) || value < 0) return "00:00";
  const rounded = Math.floor(value);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const seconds = rounded % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function showControls() {
  shell.classList.remove("controls-hidden");
  window.clearTimeout(hideTimer);
  if (!video.paused) {
    hideTimer = window.setTimeout(() => shell.classList.add("controls-hidden"), 2200);
  }
}

function setPlaybackState() {
  togglePlayback.textContent = video.paused ? "播放" : "暂停";
  playbackStatus.textContent = video.paused ? "已暂停" : "正在播放";
  statusDot.classList.toggle("ready", !video.paused);
  showControls();
}

async function playVideo() {
  try {
    await video.play();
  } catch (error) {
    playbackStatus.textContent = "等待播放";
    showControls();
  }
}

async function toggleVideo() {
  if (video.paused) {
    await playVideo();
  } else {
    video.pause();
  }
}

video.src = params.get("src") || "";
video.loop = params.get("loop") !== "0";
mediaTitle.textContent = params.get("title") || "二维码视频";
toggleLoop.textContent = video.loop ? "循环开" : "循环关";
toggleLoop.setAttribute("aria-pressed", String(video.loop));

video.addEventListener("loadedmetadata", async () => {
  duration.textContent = formatTime(video.duration);
  playbackStatus.textContent = "已就绪";
  statusDot.classList.add("ready");
  if (shouldAutoplay) await playVideo();
});
video.addEventListener("play", setPlaybackState);
video.addEventListener("pause", setPlaybackState);
video.addEventListener("timeupdate", () => {
  currentTime.textContent = formatTime(video.currentTime);
  seekBar.value = video.duration > 0 ? String(Math.round((video.currentTime / video.duration) * 1000)) : "0";
});
video.addEventListener("error", () => {
  const code = video.error?.code;
  playerErrorDetail.textContent = code ? `媒体加载失败，错误码 ${code}。` : "请检查视频文件和编码格式。";
  playerError.hidden = false;
  playbackStatus.textContent = "播放失败";
  statusDot.classList.remove("ready");
  statusDot.classList.add("error");
  showControls();
});

togglePlayback.addEventListener("click", toggleVideo);
toggleMute.addEventListener("click", () => {
  video.muted = !video.muted;
  toggleMute.textContent = video.muted ? "恢复声音" : "静音";
});
toggleLoop.addEventListener("click", () => {
  video.loop = !video.loop;
  toggleLoop.textContent = video.loop ? "循环开" : "循环关";
  toggleLoop.setAttribute("aria-pressed", String(video.loop));
});
seekBar.addEventListener("input", () => {
  if (video.duration > 0) video.currentTime = (Number(seekBar.value) / 1000) * video.duration;
});
document.getElementById("toggleFullscreen").addEventListener("click", async () => {
  await window.qrSuite.controlWindow("toggle-fullscreen");
  showControls();
});
document.getElementById("closePlayer").addEventListener("click", () => window.qrSuite.controlWindow("close"));
shell.addEventListener("mousemove", showControls);
shell.addEventListener("click", (event) => {
  if (event.target === video) toggleVideo();
});

document.addEventListener("keydown", async (event) => {
  if (event.code === "Space") {
    event.preventDefault();
    await toggleVideo();
  } else if (event.key.toLowerCase() === "f") {
    await window.qrSuite.controlWindow("toggle-fullscreen");
  } else if (event.key.toLowerCase() === "m") {
    toggleMute.click();
  } else if (event.key === "ArrowLeft") {
    video.currentTime = Math.max(0, video.currentTime - 5);
  } else if (event.key === "ArrowRight") {
    video.currentTime = Math.min(video.duration || 0, video.currentTime + 5);
  } else if (event.key === "Escape") {
    if (await window.qrSuite.isWindowFullScreen()) {
      await window.qrSuite.controlWindow("toggle-fullscreen");
    } else {
      await window.qrSuite.controlWindow("close");
    }
  }
  showControls();
});

showControls();
