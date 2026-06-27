#!/usr/bin/env python3
"""
Encode a short text snippet into one QR image, or decode one QR image back to text.
"""

from __future__ import annotations

import argparse
import base64
import sys
import zlib
from pathlib import Path
from typing import Iterable


TEXT_MAGIC = "QTXT1|"
TEXT_MAGIC_V2 = "QTXT2:"
BASE45_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
BASE45_INDEX = {char: index for index, char in enumerate(BASE45_ALPHABET)}


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


def base45_encode(data: bytes) -> str:
    output: list[str] = []
    index = 0
    while index < len(data):
        if index + 1 < len(data):
            value = data[index] * 256 + data[index + 1]
            output.append(BASE45_ALPHABET[value % 45])
            value //= 45
            output.append(BASE45_ALPHABET[value % 45])
            value //= 45
            output.append(BASE45_ALPHABET[value])
            index += 2
        else:
            value = data[index]
            output.append(BASE45_ALPHABET[value % 45])
            value //= 45
            output.append(BASE45_ALPHABET[value])
            index += 1
    return "".join(output)


def base45_decode(text: str) -> bytes:
    output = bytearray()
    index = 0
    while index < len(text):
        if index + 2 < len(text):
            c0 = BASE45_INDEX[text[index]]
            c1 = BASE45_INDEX[text[index + 1]]
            c2 = BASE45_INDEX[text[index + 2]]
            value = c0 + c1 * 45 + c2 * 45 * 45
            if value > 0xFFFF:
                raise ValueError("bad base45 triplet")
            output.append(value // 256)
            output.append(value % 256)
            index += 3
        elif index + 1 < len(text):
            c0 = BASE45_INDEX[text[index]]
            c1 = BASE45_INDEX[text[index + 1]]
            value = c0 + c1 * 45
            if value > 0xFF:
                raise ValueError("bad base45 pair")
            output.append(value)
            index += 2
        else:
            raise ValueError("bad base45 length")
    return bytes(output)


def build_text_payload_v1(text: str) -> str:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return TEXT_MAGIC + encoded


def read_text_exact(path: str, encoding: str) -> str:
    with Path(path).open("r", encoding=encoding, newline="") as handle:
        return handle.read()


def write_text_exact(path: str | Path, text: str, encoding: str) -> None:
    with Path(path).open("w", encoding=encoding, newline="") as handle:
        handle.write(text)


def build_text_payload(text: str, codec: str = "wechat") -> str:
    if codec == "wechat":
        return text

    raw = text.encode("utf-8")
    candidates: list[tuple[str, bytes]] = []
    if codec in ("auto", "base45"):
        candidates.append(("B45", raw))
    if codec in ("auto", "zlib-base45"):
        candidates.append(("Z45", zlib.compress(raw, level=9)))
    if codec == "legacy-base64":
        return build_text_payload_v1(text)
    if not candidates:
        raise ValueError(f"Unsupported text payload codec: {codec}")

    method, data = min(candidates, key=lambda item: len(base45_encode(item[1])))
    crc32 = zlib.crc32(raw) & 0xFFFFFFFF
    return f"{TEXT_MAGIC_V2}{method}:{len(raw)}:{crc32:08X}:{base45_encode(data)}"


def parse_text_payload(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
    else:
        text = raw
    if text.startswith(TEXT_MAGIC_V2):
        _magic, method, length_raw, crc_raw, encoded = text.split(":", 4)
        payload = base45_decode(encoded)
        if method == "Z45":
            payload = zlib.decompress(payload)
        elif method != "B45":
            raise ValueError(f"Unsupported text payload method: {method}")
        expected_length = int(length_raw)
        expected_crc = int(crc_raw, 16)
        if len(payload) != expected_length:
            raise ValueError("text payload length mismatch")
        actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError("text payload crc mismatch")
        return payload.decode("utf-8")
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
        text = read_text_exact(args.text_file, args.encoding)
    elif args.text is not None:
        text = args.text
    else:
        text = sys.stdin.read()

    payload = build_text_payload(text, args.codec)
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
    print(f"[INFO] Payload chars: {len(payload)}")
    print(f"[INFO] Codec: {args.codec}")
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
            kwargs = {
                "formats": zxingcpp.BarcodeFormat.QRCode,
                "try_rotate": True,
                "try_downscale": True,
                "is_pure": pure,
                "return_errors": False,
            }
            try:
                results = zxingcpp.read_barcodes(image, try_invert=True, **kwargs)
            except TypeError as exc:
                if "try_invert" not in str(exc):
                    raise
                results = zxingcpp.read_barcodes(image, **kwargs)
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
    except Exception:
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

    try:
        text = parse_text_payload(payloads[0])
    except ValueError as exc:
        print(f"[FAIL] Invalid QR text payload: {exc}", file=sys.stderr)
        return 1
    if args.output:
        write_text_exact(args.output, text, args.encoding)
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
    encode.add_argument(
        "--codec",
        choices=("wechat", "auto", "base45", "zlib-base45", "legacy-base64"),
        default="wechat",
        help="Text payload codec. wechat stores plain text for direct WeChat scan; auto chooses compressed Base45 for larger capacity but needs this decoder.",
    )
    encode.add_argument("--error-correction", choices=("L", "M", "Q", "H"), default="M", help="QR error correction level.")
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
