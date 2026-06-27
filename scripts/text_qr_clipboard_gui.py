#!/usr/bin/env python3
"""
Tkinter GUI for single-image text QR clipboard exchange.
"""

from __future__ import annotations

import queue
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import text_qr_clipboard as core


CODEC_OPTIONS = {
    "微信直扫：中英文原文": "wechat",
    "高容量压缩：需本工具解码": "auto",
}


class TextQrGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("文本二维码剪贴板")
        self.root.geometry("920x720")
        self.image_path = tk.StringVar(value=str((Path.cwd() / "text-qr.png").resolve()))
        self.codec_label = tk.StringVar(value="微信直扫：中英文原文")
        self.decode_image_path = tk.StringVar()
        self.status_queue: queue.Queue[str] = queue.Queue()

        self._build_ui()
        self._poll_status()

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)
        main.rowconfigure(3, weight=1)

        encode_frame = ttk.LabelFrame(main, text="文本 -> 一张二维码图片", padding=10)
        encode_frame.grid(row=0, column=0, sticky="ew")
        encode_frame.columnconfigure(1, weight=1)
        ttk.Label(encode_frame, text="输出图片").grid(row=0, column=0, sticky="w")
        ttk.Entry(encode_frame, textvariable=self.image_path).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(encode_frame, text="浏览", command=self.browse_output_image).grid(row=0, column=2)
        ttk.Label(encode_frame, text="编码模式").grid(row=1, column=0, sticky="w", pady=(8, 0))
        codec_box = ttk.Combobox(
            encode_frame,
            textvariable=self.codec_label,
            values=list(CODEC_OPTIONS),
            state="readonly",
            width=28,
        )
        codec_box.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=(8, 0))
        ttk.Button(encode_frame, text="从剪贴板粘贴", command=self.paste_text).grid(row=1, column=3, padx=(8, 0), pady=(8, 0))
        ttk.Button(encode_frame, text="生成图片", command=self.encode_text).grid(row=1, column=4, padx=(8, 0), pady=(8, 0))

        self.text_input = tk.Text(main, height=10, wrap=tk.WORD)
        self.text_input.grid(row=1, column=0, sticky="nsew", pady=(8, 12))

        decode_frame = ttk.LabelFrame(main, text="一张二维码图片 -> 文本", padding=10)
        decode_frame.grid(row=2, column=0, sticky="ew")
        decode_frame.columnconfigure(1, weight=1)
        ttk.Label(decode_frame, text="输入图片").grid(row=0, column=0, sticky="w")
        ttk.Entry(decode_frame, textvariable=self.decode_image_path).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(decode_frame, text="浏览", command=self.browse_decode_image).grid(row=0, column=2)
        ttk.Button(decode_frame, text="解码图片", command=self.decode_image).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(decode_frame, text="复制结果", command=self.copy_decoded_text).grid(row=0, column=4, padx=(8, 0))

        self.text_output = tk.Text(main, height=10, wrap=tk.WORD)
        self.text_output.grid(row=3, column=0, sticky="nsew", pady=(8, 8))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(main, textvariable=self.status_var).grid(row=4, column=0, sticky="w")

    def browse_output_image(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存二维码图片",
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("All files", "*.*")],
        )
        if path:
            self.image_path.set(path)

    def browse_decode_image(self) -> None:
        path = filedialog.askopenfilename(
            title="选择二维码图片",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")],
        )
        if path:
            self.decode_image_path.set(path)

    def paste_text(self) -> None:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showerror("剪贴板为空", "剪贴板里没有文本。")
            return
        self.text_input.delete("1.0", tk.END)
        self.text_input.insert("1.0", text)

    def encode_text(self) -> None:
        text = self.text_input.get("1.0", "end-1c")
        output = self.image_path.get().strip()
        if not output:
            messagebox.showerror("缺少输出路径", "请选择二维码图片输出路径。")
            return
        codec = CODEC_OPTIONS[self.codec_label.get()]
        args = core.parse_args(["encode", text, "-o", output, "--codec", codec])
        try:
            args.func(args)
        except SystemExit as exc:
            messagebox.showerror("生成失败", str(exc))
            return
        self.status_var.set(f"已生成: {Path(output).resolve()} ({self.codec_label.get()})")

    def decode_image(self) -> None:
        image = self.decode_image_path.get().strip()
        if not image:
            messagebox.showerror("缺少输入图片", "请选择要解码的二维码图片。")
            return
        payloads = []
        payloads.extend(core.decode_with_zxing(Path(image)))
        payloads.extend(core.decode_with_opencv(Path(image)))
        payloads.extend(core.decode_with_pyzbar(Path(image)))
        if not payloads:
            messagebox.showerror("解码失败", "没有识别到二维码文本。")
            return
        try:
            text = core.parse_text_payload(payloads[0])
        except ValueError as exc:
            messagebox.showerror("解码失败", f"二维码文本载荷校验失败：{exc}")
            return
        self.text_output.delete("1.0", tk.END)
        self.text_output.insert("1.0", text)
        self.status_var.set(f"已解码 {len(text)} 个字符")

    def copy_decoded_text(self) -> None:
        text = self.text_output.get("1.0", tk.END).rstrip("\n")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.status_var.set("已复制解码文本到剪贴板")

    def _poll_status(self) -> None:
        self.root.after(100, self._poll_status)


def main() -> None:
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except tk.TclError:
        pass
    TextQrGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
