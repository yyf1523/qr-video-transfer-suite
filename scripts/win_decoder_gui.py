#!/usr/bin/env python3
"""
Tkinter GUI for the Windows QR video decoder and screen recorder.
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

import win_decoder_hd as decoder_core


ROOT_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
DECODER_SCRIPT = ROOT_DIR / "scripts" / "win_decoder_hd.py"


class QueueWriter:
    def __init__(self, log_queue: queue.Queue[str | None]) -> None:
        self.log_queue = log_queue

    def write(self, text: str) -> int:
        if text:
            self.log_queue.put(text)
        return len(text)

    def flush(self) -> None:
        pass


class DecoderGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("QR Video 解码端 - Windows 11")
        self.root.geometry("1080x780")

        self.log_queue: queue.Queue[str | None] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None

        self.video_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str((Path.cwd() / "reconstructed_project").resolve()))
        self.state_var = tk.StringVar(value=str((Path.cwd() / "decode_state").resolve()))
        self.manifest_var = tk.StringVar()
        self.screen_mode_var = tk.BooleanVar(value=True)
        self.screen_60fps_var = tk.BooleanVar(value=True)
        self.screen_grid_var = tk.BooleanVar(value=False)

        self.record_screen_var = tk.BooleanVar(value=False)
        self.record_only_var = tk.BooleanVar(value=False)
        self.record_output_var = tk.StringVar(value=str((Path.cwd() / "screen_record.mp4").resolve()))
        self.record_seconds_var = tk.StringVar(value="300")
        self.record_fps_var = tk.StringVar(value="60")
        self.record_monitor_var = tk.StringVar(value="1")
        self.record_region_var = tk.StringVar()

        self.every_n_var = tk.StringVar(value="5")
        self.vote_window_var = tk.StringVar(value="1")
        self.confirm_copies_var = tk.StringVar(value="1")
        self.max_frames_var = tk.StringVar()

        self._build_ui()
        self._poll_log_queue()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(4, weight=1)

        file_frame = ttk.LabelFrame(main, text="解码输入 / 输出", padding=10)
        file_frame.grid(row=0, column=0, sticky="ew")
        file_frame.columnconfigure(1, weight=1)
        self._path_row(file_frame, 0, "视频文件", self.video_var, self.browse_video)
        self._path_row(file_frame, 1, "Manifest JSON", self.manifest_var, self.browse_manifest)
        self._path_row(file_frame, 2, "输出目录", self.output_var, self.browse_output_dir)
        self._path_row(file_frame, 3, "续传状态目录", self.state_var, self.browse_state_dir)

        options = ttk.LabelFrame(main, text="解码参数", padding=10)
        options.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for col in range(8):
            options.columnconfigure(col, weight=1)
        ttk.Checkbutton(options, text="电脑录屏模式", variable=self.screen_mode_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(options, text="60fps/5帧模式", variable=self.screen_60fps_var, command=self.apply_60fps_defaults).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(options, text="多码网格模式", variable=self.screen_grid_var, command=self.apply_grid_defaults).grid(row=0, column=2, sticky="w")
        ttk.Label(options, text="每 N 帧解一次").grid(row=0, column=3, sticky="e")
        ttk.Entry(options, textvariable=self.every_n_var, width=10).grid(row=0, column=4, sticky="ew", padx=(6, 12))
        ttk.Label(options, text="投票窗口").grid(row=0, column=5, sticky="e")
        ttk.Entry(options, textvariable=self.vote_window_var, width=10).grid(row=0, column=6, sticky="ew", padx=(6, 12))
        ttk.Label(options, text="确认次数").grid(row=1, column=0, sticky="e", pady=(8, 0))
        ttk.Entry(options, textvariable=self.confirm_copies_var, width=10).grid(row=1, column=1, sticky="ew", padx=(6, 12), pady=(8, 0))
        ttk.Label(options, text="最多帧数").grid(row=1, column=2, sticky="e", pady=(8, 0))
        ttk.Entry(options, textvariable=self.max_frames_var, width=10).grid(row=1, column=3, sticky="ew", padx=(6, 12), pady=(8, 0))

        record = ttk.LabelFrame(main, text="屏幕录制", padding=10)
        record.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for col in range(8):
            record.columnconfigure(col, weight=1)
        ttk.Checkbutton(record, text="先录屏", variable=self.record_screen_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(record, text="只录不解", variable=self.record_only_var).grid(row=0, column=1, sticky="w")
        ttk.Label(record, text="录制秒数").grid(row=0, column=2, sticky="e")
        ttk.Entry(record, textvariable=self.record_seconds_var, width=10).grid(row=0, column=3, sticky="ew", padx=(6, 12))
        ttk.Label(record, text="录制 FPS").grid(row=0, column=4, sticky="e")
        ttk.Entry(record, textvariable=self.record_fps_var, width=10).grid(row=0, column=5, sticky="ew", padx=(6, 12))
        ttk.Label(record, text="屏幕编号").grid(row=0, column=6, sticky="e")
        ttk.Entry(record, textvariable=self.record_monitor_var, width=8).grid(row=0, column=7, sticky="ew", padx=(6, 0))

        ttk.Label(record, text="录制输出").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(record, textvariable=self.record_output_var).grid(row=1, column=1, columnspan=5, sticky="ew", padx=(8, 8), pady=(8, 0))
        ttk.Button(record, text="浏览", command=self.browse_record_output).grid(row=1, column=6, sticky="ew", pady=(8, 0))
        ttk.Label(record, text="区域 left,top,width,height").grid(row=2, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(record, textvariable=self.record_region_var).grid(row=2, column=1, columnspan=5, sticky="ew", padx=(8, 8), pady=(8, 0))

        hint = ttk.Label(
            main,
            text="录屏时先在另一块屏幕或播放器中全屏播放加码视频；录制完成后会自动调用同一个解码器。",
            foreground="#555555",
        )
        hint.grid(row=3, column=0, sticky="w", pady=(8, 0))

        log_frame = ttk.LabelFrame(main, text="运行日志", padding=10)
        log_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=16, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(main)
        buttons.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(0, weight=1)
        self.run_button = ttk.Button(buttons, text="开始解码 / 录屏", command=self.start_decode)
        self.run_button.grid(row=0, column=1, padx=(8, 0))
        self.stop_button = ttk.Button(buttons, text="停止", command=self.stop_process, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=2, padx=(8, 0))
        ttk.Button(buttons, text="打开输出目录", command=self.open_output_dir).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(buttons, text="打开状态目录", command=self.open_state_dir).grid(row=0, column=4, padx=(8, 0))

    def _path_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, command) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(parent, text="浏览", command=command).grid(row=row, column=2, pady=3)

    def apply_60fps_defaults(self) -> None:
        if self.screen_60fps_var.get():
            self.screen_mode_var.set(True)
            self.record_fps_var.set("60")
            self.every_n_var.set("5")
            self.vote_window_var.set("1")

    def apply_grid_defaults(self) -> None:
        if self.screen_grid_var.get():
            self.screen_mode_var.set(True)
            self.every_n_var.set("1")
            self.vote_window_var.set("1")

    def browse_video(self) -> None:
        path = filedialog.askopenfilename(title="选择录屏视频", filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"), ("All files", "*.*")])
        if path:
            self.video_var.set(path)

    def browse_manifest(self) -> None:
        path = filedialog.askopenfilename(title="选择 Manifest JSON", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.manifest_var.set(path)

    def browse_output_dir(self) -> None:
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_var.set(path)

    def browse_state_dir(self) -> None:
        path = filedialog.askdirectory(title="选择续传状态目录")
        if path:
            self.state_var.set(path)

    def browse_record_output(self) -> None:
        path = filedialog.asksaveasfilename(title="录屏输出", defaultextension=".mp4", filetypes=[("MP4", "*.mp4"), ("AVI", "*.avi")])
        if path:
            self.record_output_var.set(path)

    def build_command(self) -> list[str]:
        command = [sys.executable, str(DECODER_SCRIPT)]

        if self.record_screen_var.get():
            command.append("--record-screen")
            if self.record_only_var.get():
                command.append("--record-only")
            self._append_value(command, "--record-output", self.record_output_var.get())
            self._append_value(command, "--record-seconds", self.record_seconds_var.get())
            self._append_value(command, "--record-fps", self.record_fps_var.get())
            self._append_value(command, "--record-monitor", self.record_monitor_var.get())
            self._append_value(command, "--record-region", self.record_region_var.get())
        else:
            video = self.video_var.get().strip()
            if not video:
                raise ValueError("请选择录屏视频，或勾选“先录屏”")
            command.append(video)

        if self.screen_mode_var.get():
            command.append("--screen-mode")
        if self.screen_60fps_var.get():
            command.append("--screen-60fps")
        if self.screen_grid_var.get():
            command.append("--screen-grid")
        self._append_value(command, "-o", self.output_var.get())
        self._append_value(command, "--state-dir", self.state_var.get())
        self._append_value(command, "--manifest-json", self.manifest_var.get())
        self._append_value(command, "--every-n", self.every_n_var.get())
        self._append_value(command, "--vote-window", self.vote_window_var.get())
        self._append_value(command, "--confirm-copies", self.confirm_copies_var.get())
        self._append_value(command, "--max-frames", self.max_frames_var.get())
        return command

    def _append_value(self, command: list[str], flag: str, value: str) -> None:
        value = value.strip()
        if value:
            command.extend([flag, value])

    def start_decode(self) -> None:
        if self.process is not None:
            messagebox.showinfo("正在运行", "已有任务正在运行")
            return
        try:
            command = self.build_command()
        except ValueError as exc:
            messagebox.showerror("参数缺失", str(exc))
            return

        Path(self.output_var.get()).mkdir(parents=True, exist_ok=True)
        Path(self.state_var.get()).mkdir(parents=True, exist_ok=True)
        record_output = self.record_output_var.get().strip()
        if record_output:
            Path(record_output).parent.mkdir(parents=True, exist_ok=True)

        self.log_text.delete("1.0", tk.END)
        self._log("[COMMAND] " + " ".join(f'"{part}"' if " " in part else part for part in command) + "\n")
        self.run_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        runner = self._run_in_process if getattr(sys, "frozen", False) else self._run_process
        threading.Thread(target=runner, args=(command,), daemon=True).start()

    def _run_in_process(self, command: list[str]) -> None:
        try:
            argv = command[2:]
            writer = QueueWriter(self.log_queue)
            with redirect_stdout(writer), redirect_stderr(writer):
                code = decoder_core.main(argv)
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
        self._open_dir(Path(self.output_var.get().strip() or "."))

    def open_state_dir(self) -> None:
        self._open_dir(Path(self.state_var.get().strip() or "."))

    def _open_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
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
    DecoderGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
