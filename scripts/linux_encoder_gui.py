#!/usr/bin/env python3
"""
Tkinter GUI for the Linux/UOS QR video encoder.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import linux_encoder_hd as encoder_core


ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
ENCODER_SCRIPT = ROOT_DIR / "scripts" / "linux_encoder_hd.py"


PROFILES = {
    "screen_1080p_horizontal_4qr": {
        "label": "本机 1080p 横向 4QR",
        "qr_version": "40",
        "box_size": "2",
        "chunk_size": "1200",
        "payload_mode": "binary",
        "qr_error_correction": "H",
        "grid_cols": "4",
        "grid_rows": "1",
        "grid_gap": "10",
        "canvas_width": "1920",
        "canvas_height": "1080",
        "fps": "60",
        "repeat": "5",
        "passes": "1",
        "label_height": "64",
        "label_scale": "0.45",
        "meta_qr_size": "48",
        "meta_qr_version": "6",
        "color_border": "8",
        "outer_white": "4",
    },
    "screen_dense_2x2": {
        "label": "电脑录屏 2x2 高密度模式",
        "qr_version": "40",
        "box_size": "2",
        "chunk_size": "1200",
        "payload_mode": "binary",
        "qr_error_correction": "H",
        "grid_cols": "2",
        "grid_rows": "2",
        "grid_gap": "16",
        "canvas_width": "",
        "canvas_height": "",
        "fps": "60",
        "repeat": "5",
        "passes": "1",
        "label_height": "84",
        "label_scale": "0.55",
        "meta_qr_size": "64",
        "meta_qr_version": "6",
        "color_border": "20",
        "outer_white": "10",
    },
    "screen_60fps": {
        "label": "电脑录屏 60fps 高速模式",
        "qr_version": "40",
        "box_size": "4",
        "chunk_size": "850",
        "payload_mode": "ascii",
        "qr_error_correction": "H",
        "grid_cols": "1",
        "grid_rows": "1",
        "grid_gap": "16",
        "canvas_width": "",
        "canvas_height": "",
        "fps": "60",
        "repeat": "5",
        "passes": "1",
        "label_height": "112",
        "label_scale": "0.8",
        "meta_qr_size": "96",
        "meta_qr_version": "6",
        "color_border": "36",
        "outer_white": "18",
    },
    "screen_1080": {
        "label": "电脑录屏 1080p 适配",
        "qr_version": "40",
        "box_size": "4",
        "chunk_size": "850",
        "payload_mode": "ascii",
        "qr_error_correction": "H",
        "grid_cols": "1",
        "grid_rows": "1",
        "grid_gap": "16",
        "canvas_width": "",
        "canvas_height": "",
        "fps": "10",
        "repeat": "2",
        "passes": "1",
        "label_height": "112",
        "label_scale": "0.8",
        "meta_qr_size": "96",
        "meta_qr_version": "6",
        "color_border": "36",
        "outer_white": "18",
    },
    "screen_1600": {
        "label": "电脑录屏 2.5K/1600 高度",
        "qr_version": "40",
        "box_size": "6",
        "chunk_size": "850",
        "payload_mode": "ascii",
        "qr_error_correction": "H",
        "grid_cols": "1",
        "grid_rows": "1",
        "grid_gap": "16",
        "canvas_width": "",
        "canvas_height": "",
        "fps": "10",
        "repeat": "2",
        "passes": "1",
        "label_height": "128",
        "label_scale": "1.0",
        "meta_qr_size": "112",
        "meta_qr_version": "6",
        "color_border": "42",
        "outer_white": "22",
    },
    "phone_safe": {
        "label": "手机拍屏稳妥模式",
        "qr_version": "20",
        "box_size": "8",
        "chunk_size": "180",
        "payload_mode": "ascii",
        "qr_error_correction": "H",
        "grid_cols": "1",
        "grid_rows": "1",
        "grid_gap": "16",
        "canvas_width": "",
        "canvas_height": "",
        "fps": "6",
        "repeat": "3",
        "passes": "2",
        "label_height": "128",
        "label_scale": "1.0",
        "meta_qr_size": "112",
        "meta_qr_version": "4",
        "color_border": "44",
        "outer_white": "24",
    },
}
PROFILE_LABEL_TO_KEY = {profile["label"]: key for key, profile in PROFILES.items()}


class QueueWriter:
    def __init__(self, log_queue: queue.Queue[str | None]) -> None:
        self.log_queue = log_queue

    def write(self, text: str) -> int:
        if text:
            self.log_queue.put(text)
        return len(text)

    def flush(self) -> None:
        pass


class EncoderGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("QR Video 加码端 - UOS/Linux")
        self.root.geometry("1060x760")

        self.log_queue: queue.Queue[str | None] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.lib_dirs: list[str] = []

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str((Path.cwd() / "qr-video-out" / "hd_secure_stream.mp4").resolve()))
        self.manifest_var = tk.StringVar()
        self.manifest_json_var = tk.StringVar()
        self.resume_var = tk.StringVar()
        self.preview_var = tk.StringVar()
        self.profile_var = tk.StringVar(value=PROFILES["screen_60fps"]["label"])

        self.option_vars = {
            "qr_version": tk.StringVar(),
            "box_size": tk.StringVar(),
            "chunk_size": tk.StringVar(),
            "payload_mode": tk.StringVar(),
            "qr_error_correction": tk.StringVar(),
            "grid_cols": tk.StringVar(),
            "grid_rows": tk.StringVar(),
            "grid_gap": tk.StringVar(),
            "canvas_width": tk.StringVar(),
            "canvas_height": tk.StringVar(),
            "fps": tk.StringVar(),
            "repeat": tk.StringVar(),
            "passes": tk.StringVar(),
            "label_height": tk.StringVar(),
            "label_scale": tk.StringVar(),
            "meta_qr_size": tk.StringVar(),
            "meta_qr_version": tk.StringVar(),
            "color_border": tk.StringVar(),
            "outer_white": tk.StringVar(),
        }

        self._build_ui()
        self.apply_profile()
        self._poll_log_queue()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        file_frame = ttk.LabelFrame(main, text="输入 / 输出", padding=10)
        file_frame.grid(row=0, column=0, sticky="ew")
        file_frame.columnconfigure(1, weight=1)

        self._path_row(file_frame, 0, "源码压缩包", self.source_var, self.browse_source)
        self._path_row(file_frame, 1, "输出视频", self.output_var, self.browse_output)
        self._path_row(file_frame, 2, "Manifest MD", self.manifest_var, self.browse_manifest)
        self._path_row(file_frame, 3, "Manifest JSON", self.manifest_json_var, self.browse_manifest_json)
        self._path_row(file_frame, 4, "补片 JSON", self.resume_var, self.browse_resume)

        lib_frame = ttk.LabelFrame(main, text="Jar 目录", padding=10)
        lib_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        lib_frame.columnconfigure(0, weight=1)
        self.lib_list = tk.Listbox(lib_frame, height=4)
        self.lib_list.grid(row=0, column=0, rowspan=3, sticky="ew")
        ttk.Button(lib_frame, text="添加目录", command=self.add_lib_dir).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(lib_frame, text="移除选中", command=self.remove_lib_dir).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)
        ttk.Button(lib_frame, text="清空", command=self.clear_lib_dirs).grid(row=2, column=1, sticky="ew", padx=(8, 0))

        profile_frame = ttk.LabelFrame(main, text="视频参数", padding=10)
        profile_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for col in range(8):
            profile_frame.columnconfigure(col, weight=1)

        ttk.Label(profile_frame, text="预设").grid(row=0, column=0, sticky="w")
        profile = ttk.Combobox(
            profile_frame,
            textvariable=self.profile_var,
            values=[profile["label"] for profile in PROFILES.values()],
            state="readonly",
            width=22,
        )
        profile.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        profile.bind("<<ComboboxSelected>>", lambda _event: self.apply_profile())
        ttk.Button(profile_frame, text="应用预设", command=self.apply_profile).grid(row=0, column=2, sticky="ew")
        ttk.Label(profile_frame, textvariable=tk.StringVar(value="")).grid(row=0, column=3)

        labels = [
            ("qr_version", "QR版本"),
            ("box_size", "模块像素"),
            ("chunk_size", "每片字节"),
            ("payload_mode", "载荷模式"),
            ("qr_error_correction", "纠错等级"),
            ("grid_cols", "网格列数"),
            ("grid_rows", "网格行数"),
            ("grid_gap", "网格间距"),
            ("canvas_width", "画布宽度"),
            ("canvas_height", "画布高度"),
            ("fps", "FPS"),
            ("repeat", "单片重复"),
            ("passes", "整体轮次"),
            ("label_height", "顶部高度"),
            ("label_scale", "数字字号"),
            ("meta_qr_size", "小QR尺寸"),
            ("meta_qr_version", "小QR版本"),
            ("color_border", "红框宽度"),
            ("outer_white", "外白边"),
        ]
        for index, (key, label) in enumerate(labels):
            row = 1 + index // 4
            col = (index % 4) * 2
            ttk.Label(profile_frame, text=label).grid(row=row, column=col, sticky="w", pady=(8, 0))
            ttk.Entry(profile_frame, textvariable=self.option_vars[key], width=10).grid(
                row=row, column=col + 1, sticky="ew", padx=(6, 12), pady=(8, 0)
            )

        extra_frame = ttk.Frame(main)
        extra_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        extra_frame.columnconfigure(1, weight=1)
        ttk.Label(extra_frame, text="仅预览前 N 片").grid(row=0, column=0, sticky="w")
        ttk.Entry(extra_frame, textvariable=self.preview_var, width=12).grid(row=0, column=1, sticky="w", padx=(8, 0))

        log_frame = ttk.LabelFrame(main, text="运行日志", padding=10)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=14, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(main)
        buttons.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(0, weight=1)
        self.run_button = ttk.Button(buttons, text="开始生成视频", command=self.start_encode)
        self.run_button.grid(row=0, column=1, padx=(8, 0))
        self.stop_button = ttk.Button(buttons, text="停止", command=self.stop_process, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(buttons, text="打开输出目录", command=self.open_output_dir).grid(row=0, column=3, padx=(8, 0))

    def _path_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(parent, text="浏览", command=command).grid(row=row, column=2, pady=3)

    def apply_profile(self) -> None:
        profile_key = PROFILE_LABEL_TO_KEY.get(self.profile_var.get(), "screen_60fps")
        profile = PROFILES[profile_key]
        for key, var in self.option_vars.items():
            var.set(profile[key])
        self._log(f"[INFO] 已应用预设: {profile['label']}\n")

    def browse_source(self) -> None:
        path = filedialog.askopenfilename(
            title="选择源码压缩包",
            filetypes=[("Archive", "*.zip *.tar.gz *.tgz *.tar *.7z"), ("All files", "*.*")],
        )
        if path:
            self.source_var.set(path)
            output = Path(path).with_name("hd_secure_stream.mp4")
            self.output_var.set(str(output))

    def browse_output(self) -> None:
        path = filedialog.asksaveasfilename(title="输出视频", defaultextension=".mp4", filetypes=[("MP4", "*.mp4"), ("AVI", "*.avi")])
        if path:
            self.output_var.set(path)

    def browse_manifest(self) -> None:
        path = filedialog.asksaveasfilename(title="Manifest MD", defaultextension=".md", filetypes=[("Markdown", "*.md")])
        if path:
            self.manifest_var.set(path)

    def browse_manifest_json(self) -> None:
        path = filedialog.asksaveasfilename(title="Manifest JSON", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            self.manifest_json_var.set(path)

    def browse_resume(self) -> None:
        path = filedialog.askopenfilename(title="选择 missing_chunks.json", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.resume_var.set(path)

    def add_lib_dir(self) -> None:
        path = filedialog.askdirectory(title="选择 Jar 目录")
        if path and path not in self.lib_dirs:
            self.lib_dirs.append(path)
            self.lib_list.insert(tk.END, path)

    def remove_lib_dir(self) -> None:
        for index in reversed(self.lib_list.curselection()):
            self.lib_list.delete(index)
            del self.lib_dirs[index]

    def clear_lib_dirs(self) -> None:
        self.lib_dirs.clear()
        self.lib_list.delete(0, tk.END)

    def build_command(self) -> list[str]:
        source = self.source_var.get().strip()
        output = self.output_var.get().strip()
        if not source:
            raise ValueError("请选择源码压缩包")
        if not output:
            raise ValueError("请选择输出视频路径")

        command = [sys.executable, str(ENCODER_SCRIPT), "--source", source]
        for lib_dir in self.lib_dirs:
            command.extend(["--lib-dir", lib_dir])
        command.extend(["-o", output])

        manifest = self.manifest_var.get().strip()
        manifest_json = self.manifest_json_var.get().strip()
        if manifest:
            command.extend(["--manifest", manifest])
        if manifest_json:
            command.extend(["--manifest-json", manifest_json])
        resume = self.resume_var.get().strip()
        if resume:
            command.extend(["--resume-missing", resume])
        preview = self.preview_var.get().strip()
        if preview:
            command.extend(["--preview-chunks", preview])

        option_map = {
            "qr_version": "--qr-version",
            "box_size": "--box-size",
            "chunk_size": "--chunk-size",
            "payload_mode": "--payload-mode",
            "qr_error_correction": "--qr-error-correction",
            "grid_cols": "--grid-cols",
            "grid_rows": "--grid-rows",
            "grid_gap": "--grid-gap",
            "canvas_width": "--canvas-width",
            "canvas_height": "--canvas-height",
            "fps": "--fps",
            "repeat": "--repeat",
            "passes": "--passes",
            "label_height": "--label-height",
            "label_scale": "--label-scale",
            "meta_qr_size": "--meta-qr-size",
            "meta_qr_version": "--meta-qr-version",
            "color_border": "--color-border",
            "outer_white": "--outer-white",
        }
        for key, flag in option_map.items():
            value = self.option_vars[key].get().strip()
            if value:
                command.extend([flag, value])
        return command

    def start_encode(self) -> None:
        if self.process is not None:
            messagebox.showinfo("正在运行", "已有任务正在运行")
            return
        try:
            command = self.build_command()
        except ValueError as exc:
            messagebox.showerror("参数缺失", str(exc))
            return

        Path(self.output_var.get()).parent.mkdir(parents=True, exist_ok=True)
        self.log_text.delete("1.0", tk.END)
        self._log("[COMMAND] " + " ".join(f'"{part}"' if " " in part else part for part in command) + "\n")
        self.run_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        runner = self._run_in_process if getattr(sys, "frozen", False) else self._run_process
        thread = threading.Thread(target=runner, args=(command,), daemon=True)
        thread.start()

    def _run_in_process(self, command: list[str]) -> None:
        try:
            argv = command[2:]
            writer = QueueWriter(self.log_queue)
            with redirect_stdout(writer), redirect_stderr(writer):
                code = encoder_core.main(argv)
            self.log_queue.put(f"\n[EXIT] {code}\n")
        except SystemExit as exc:
            self.log_queue.put(f"\n[EXIT] {exc.code}\n")
        except Exception as exc:  # pragma: no cover - GUI error path
            self.log_queue.put(f"\n[ERROR] {exc}\n")
        finally:
            self.process = None
            self.log_queue.put(None)

    def _run_process(self, command: list[str]) -> None:
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(ROOT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.log_queue.put(line)
            code = self.process.wait()
            self.log_queue.put(f"\n[EXIT] {code}\n")
        except Exception as exc:  # pragma: no cover - GUI error path
            self.log_queue.put(f"\n[ERROR] {exc}\n")
        finally:
            self.process = None
            self.log_queue.put(None)

    def stop_process(self) -> None:
        if self.process is not None:
            self.process.terminate()
            self._log("[INFO] 已请求停止任务\n")

    def open_output_dir(self) -> None:
        target = Path(self.output_var.get().strip() or ".").expanduser()
        path = target.parent if target.suffix else target
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _log(self, text: str) -> None:
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                item = self.log_queue.get_nowait()
                if item is None:
                    self.run_button.configure(state=tk.NORMAL)
                    self.stop_button.configure(state=tk.DISABLED)
                else:
                    self._log(item)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)


def main() -> None:
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except tk.TclError:
        pass
    EncoderGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
