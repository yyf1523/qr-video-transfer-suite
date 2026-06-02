#!/usr/bin/env python3
"""
Encode a short text snippet into one QR image, or decode one QR image back to text.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path
from typing import Iterable


TEXT_MAGIC = "QTXT1|"


def read_clipboard_text() -> str:
    try:
        import tkinter as tk
    except ImportError as exc:
        raise SystemExit("tkinter is required for clipboard access.") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    except tk.TclError as exc:
        raise SystemExit("Clipboard does not contain text.") from exc
    finally:
        root.destroy()


def write_clipboard_text(text: str) -> None:
    try:
        import tkinter as tk
    except ImportError as exc:
        raise SystemExit("tkinter is required for clipboard access.") from exc

    root = tk.Tk()
    root.withdraw()
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    root.destroy()


def build_text_payload(text: str) -> str:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return TEXT_MAGIC + encoded


def parse_text_payload(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
    else:
        text = raw
    if text.startswith(TEXT_MAGIC):
        encoded = text[len(TEXT_MAGIC) :]
        return base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    return text


def encode_text(args: argparse.Namespace) -> int:
    try:
        import qrcode
    except ImportError as exc:
        raise SystemExit("Missing dependency. Install qrcode and Pillow first.") from exc

    if args.clipboard:
        text = read_clipboard_text()
    elif args.text_file:
        text = Path(args.text_file).read_text(encoding=args.encoding)
    elif args.text is not None:
        text = args.text
    else:
        text = sys.stdin.read()

    payload = build_text_payload(text)
    correction = {
        "L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H,
    }[args.error_correction]

    qr = qrcode.QRCode(
        version=args.qr_version,
        error_correction=correction,
        box_size=args.box_size,
        border=args.border,
    )
    try:
        qr.add_data(payload, optimize=0)
        qr.make(fit=args.qr_version is None)
    except Exception as exc:
        raise SystemExit(
            "Text is too large for one QR image. Shorten it, lower error correction, "
            "or use the video transfer mode."
        ) from exc

    image = qr.make_image(fill_color=args.fill_color, back_color=args.back_color).convert("RGB")
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    print(f"[DONE] QR image written: {output_path.resolve()}")
    print(f"[INFO] Text chars: {len(text)}")
    print(f"[INFO] Payload bytes: {len(payload.encode('utf-8'))}")
    return 0


def decode_with_zxing(path: Path) -> list[bytes]:
    try:
        import cv2
        import zxingcpp
    except ImportError:
        return []

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return []
    payloads: list[bytes] = []
    for pure in (False, True):
        try:
            results = zxingcpp.read_barcodes(
                image,
                formats=zxingcpp.BarcodeFormat.QRCode,
                try_rotate=True,
                try_downscale=True,
                try_invert=True,
                is_pure=pure,
                return_errors=False,
            )
        except Exception:
            continue
        for result in results:
            data = bytes(result.bytes) if result.bytes else result.text.encode("utf-8", errors="ignore")
            if data:
                payloads.append(data)
    return payloads


def decode_with_opencv(path: Path) -> list[bytes]:
    try:
        import cv2
    except ImportError:
        return []

    image = cv2.imread(str(path))
    if image is None:
        return []
    detector = cv2.QRCodeDetector()
    payloads: list[bytes] = []
    try:
        ok, decoded_info, _points, _ = detector.detectAndDecodeMulti(image)
        if ok:
            payloads.extend(text.encode("utf-8") for text in decoded_info if text)
    except Exception:
        pass
    try:
        text, _points, _ = detector.detectAndDecode(image)
        if text:
            payloads.append(text.encode("utf-8"))
    except Exception:
        pass
    return payloads


def decode_with_pyzbar(path: Path) -> list[bytes]:
    try:
        from PIL import Image
        from pyzbar.pyzbar import decode
        from pyzbar.wrapper import ZBarSymbol
    except ImportError:
        return []

    try:
        image = Image.open(path)
    except Exception:
        return []
    return [result.data for result in decode(image, symbols=[ZBarSymbol.QRCODE]) if result.data]


def decode_image(args: argparse.Namespace) -> int:
    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Image not found: {image_path}", file=sys.stderr)
        return 2

    payloads = []
    payloads.extend(decode_with_zxing(image_path))
    payloads.extend(decode_with_opencv(image_path))
    payloads.extend(decode_with_pyzbar(image_path))
    if not payloads:
        print("[FAIL] No QR text decoded.", file=sys.stderr)
        return 1

    text = parse_text_payload(payloads[0])
    if args.output:
        Path(args.output).write_text(text, encoding=args.encoding)
        print(f"[DONE] Text written: {Path(args.output).resolve()}")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")

    if args.copy:
        write_clipboard_text(text)
        print("[DONE] Text copied to clipboard.")
    print(f"[INFO] Text chars: {len(text)}")
    return 0


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Single-image text QR clipboard helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    encode = subparsers.add_parser("encode", help="Encode text into one QR image.")
    encode.add_argument("text", nargs="?", help="Text to encode. If omitted, stdin is used.")
    encode.add_argument("-o", "--output", default="text-qr.png", help="Output PNG path.")
    encode.add_argument("--text-file", help="Read text from a UTF-8 text file.")
    encode.add_argument("--clipboard", action="store_true", help="Read text from the system clipboard.")
    encode.add_argument("--encoding", default="utf-8", help="Text file encoding.")
    encode.add_argument("--qr-version", type=int, help="Fixed QR version 1-40. Default auto-fits.")
    encode.add_argument("--error-correction", choices=("L", "M", "Q", "H"), default="Q", help="QR error correction level.")
    encode.add_argument("--box-size", type=int, default=10, help="Pixels per QR module.")
    encode.add_argument("--border", type=int, default=4, help="Quiet-zone modules.")
    encode.add_argument("--fill-color", default="black", help="QR foreground color.")
    encode.add_argument("--back-color", default="white", help="QR background color.")
    encode.set_defaults(func=encode_text)

    decode = subparsers.add_parser("decode", help="Decode text from one QR image.")
    decode.add_argument("image", help="QR image path.")
    decode.add_argument("-o", "--output", help="Write decoded text to this file.")
    decode.add_argument("--copy", action="store_true", help="Copy decoded text to the system clipboard.")
    decode.add_argument("--encoding", default="utf-8", help="Output text file encoding.")
    decode.set_defaults(func=decode_image)

    return parser.parse_args(list(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
