#!/usr/bin/env python3
"""
Recover files from a phone-recorded QR-code video.

Receiver side: Windows/Python. The script finds the red locator frame, applies
perspective correction, decodes QR payloads, validates CRC32, deduplicates
chunks, and reconstructs source archives plus lib/*.jar files.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Iterable


MAGIC = b"QVT1"
ASCII_MAGIC = b"QVT2"
PROTOCOL_VERSION = 1
FILE_TYPE_SOURCE = 0x01
FILE_TYPE_JAR = 0x02
HEADER_STRUCT = struct.Struct(">4sBBIIIHH")
HEADER_SIZE = HEADER_STRUCT.size


@dataclass
class FileRecord:
    file_type: int
    total: int
    chunks: dict[int, bytes] = field(default_factory=dict)


def state_key(file_type: int, file_name: str) -> str:
    return f"{file_type}:{safe_relative_name(file_name)}"


def state_token(file_type: int, file_name: str) -> str:
    raw = state_key(file_type, file_name).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_state_token(token: str) -> tuple[int, str]:
    padded = token + "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    file_type, file_name = raw.split(":", 1)
    return int(file_type), safe_relative_name(file_name)


@dataclass
class DecodeStats:
    video_frames: int = 0
    qr_candidates: int = 0
    accepted_chunks: int = 0
    duplicate_chunks: int = 0
    crc_failures: int = 0
    protocol_rejects: int = 0


def safe_relative_name(raw_name: str) -> str:
    raw_name = raw_name.replace("\\", "/")
    path = PurePosixPath(raw_name)
    if path.is_absolute():
        raise ValueError(f"Absolute path is not allowed: {raw_name!r}")
    parts = []
    for part in path.parts:
        if part in ("", ".", ".."):
            continue
        parts.append(part)
    if not parts:
        raise ValueError(f"Invalid file name: {raw_name!r}")
    return "/".join(parts)


def parse_payload(raw_bytes: bytes):
    if raw_bytes.startswith(ASCII_MAGIC + b"|"):
        return parse_ascii_payload(raw_bytes)

    if len(raw_bytes) < HEADER_SIZE:
        raise ValueError("payload too short")

    magic, version, file_type, index, total, expected_crc, payload_len, name_len = HEADER_STRUCT.unpack_from(raw_bytes, 0)
    if magic != MAGIC:
        raise ValueError("bad magic")
    if version != PROTOCOL_VERSION:
        raise ValueError("bad protocol version")
    if file_type not in (FILE_TYPE_SOURCE, FILE_TYPE_JAR):
        raise ValueError("bad file type")
    if total < 1 or index >= total:
        raise ValueError("bad chunk index")

    name_start = HEADER_SIZE
    name_end = name_start + name_len
    payload_start = name_end
    payload_end = payload_start + payload_len
    if payload_end != len(raw_bytes):
        raise ValueError("payload length mismatch")

    file_name = safe_relative_name(raw_bytes[name_start:name_end].decode("utf-8"))
    payload = raw_bytes[payload_start:payload_end]
    actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ValueError("crc mismatch")

    return file_type, index, total, file_name, payload


def parse_ascii_payload(raw_bytes: bytes):
    parts = raw_bytes.split(b"|", 8)
    if len(parts) != 9:
        raise ValueError("bad ascii protocol field count")
    magic, version_raw, file_type_raw, index_raw, total_raw, crc_raw, payload_len_raw, name_b64, payload_b64 = parts
    if magic != ASCII_MAGIC:
        raise ValueError("bad ascii magic")

    version = int(version_raw)
    file_type = int(file_type_raw)
    index = int(index_raw)
    total = int(total_raw)
    expected_crc = int(crc_raw, 16)
    payload_len = int(payload_len_raw)

    if version != PROTOCOL_VERSION:
        raise ValueError("bad protocol version")
    if file_type not in (FILE_TYPE_SOURCE, FILE_TYPE_JAR):
        raise ValueError("bad file type")
    if total < 1 or index >= total:
        raise ValueError("bad chunk index")

    file_name = safe_relative_name(base64.b64decode(name_b64).decode("utf-8"))
    payload = base64.b64decode(payload_b64)
    if len(payload) != payload_len:
        raise ValueError("payload length mismatch")
    actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ValueError("crc mismatch")

    return file_type, index, total, file_name, payload


def parse_meta_payload(raw_bytes: bytes):
    if not raw_bytes.startswith(b"QVM1|"):
        raise ValueError("not metadata")
    parts = raw_bytes.split(b"|", 7)
    if len(parts) != 8:
        raise ValueError("bad metadata field count")
    _magic, version_raw, file_type_raw, index_raw, total_raw, crc_raw, payload_len_raw, name_b64 = parts
    version = int(version_raw)
    if version != PROTOCOL_VERSION:
        raise ValueError("bad metadata version")
    return {
        "file_type": int(file_type_raw),
        "index": int(index_raw),
        "total": int(total_raw),
        "crc32": int(crc_raw, 16),
        "payload_len": int(payload_len_raw),
        "file_name": safe_relative_name(base64.b64decode(name_b64).decode("utf-8")),
    }


def order_points(points):
    import numpy as np

    rect = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    rect[0] = points[np.argmin(sums)]
    rect[2] = points[np.argmax(sums)]
    diffs = np.diff(points, axis=1)
    rect[1] = points[np.argmin(diffs)]
    rect[3] = points[np.argmax(diffs)]
    return rect


def red_frame_warps(frame, args):
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower_red_1 = np.array([0, args.red_saturation_min, args.red_value_min])
    upper_red_1 = np.array([12, 255, 255])
    lower_red_2 = np.array([168, args.red_saturation_min, args.red_value_min])
    upper_red_2 = np.array([180, 255, 255])
    mask = cv2.inRange(hsv, lower_red_1, upper_red_1) | cv2.inRange(hsv, lower_red_2, upper_red_2)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.dilate(mask, kernel, iterations=1)

    contours_info = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = contours_info[0] if len(contours_info) == 2 else contours_info[1]
    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    for contour in contours[: args.max_contours]:
        area = cv2.contourArea(contour)
        if area < args.min_red_area:
            continue

        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4:
            points = approx.reshape(4, 2).astype("float32")
        else:
            rect = cv2.minAreaRect(contour)
            points = cv2.boxPoints(rect).astype("float32")

        ordered = order_points(points)
        width_a = np.linalg.norm(ordered[2] - ordered[3])
        width_b = np.linalg.norm(ordered[1] - ordered[0])
        height_a = np.linalg.norm(ordered[1] - ordered[2])
        height_b = np.linalg.norm(ordered[0] - ordered[3])
        max_width = max(width_a, width_b)
        max_height = max(height_a, height_b)
        if min(max_width, max_height) <= 0:
            continue
        aspect = max_width / max_height
        if aspect < 0.65 or aspect > 1.55:
            continue

        dst_size = args.warp_size
        dst = np.array(
            [[0, 0], [dst_size - 1, 0], [dst_size - 1, dst_size - 1], [0, dst_size - 1]],
            dtype="float32",
        )
        matrix = cv2.getPerspectiveTransform(ordered, dst)
        warped = cv2.warpPerspective(frame, matrix, (dst_size, dst_size))

        crop = int(dst_size * args.crop_ratio)
        if crop > 0 and crop * 2 < dst_size:
            warped = warped[crop : dst_size - crop, crop : dst_size - crop]

        pad = args.decode_padding
        if pad > 0:
            warped = cv2.copyMakeBorder(
                warped,
                pad,
                pad,
                pad,
                pad,
                cv2.BORDER_CONSTANT,
                value=(255, 255, 255),
            )

        yield warped


def decode_qr_images(images):
    import cv2
    import numpy as np
    from pyzbar.pyzbar import decode
    from pyzbar.wrapper import ZBarSymbol

    def decode_with_zxing(candidate):
        try:
            import zxingcpp
        except ImportError:
            return []
        payloads = []
        for pure in (False, True):
            try:
                results = zxingcpp.read_barcodes(
                    candidate,
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

    def decode_with_opencv(candidate):
        detector = cv2.QRCodeDetector()
        payloads = []
        try:
            ok, decoded_info, _points, _ = detector.detectAndDecodeMulti(candidate)
            if ok:
                payloads.extend(text.encode("utf-8", errors="ignore") for text in decoded_info if text)
        except Exception:
            pass
        try:
            text, _points, _ = detector.detectAndDecode(candidate)
            if text:
                payloads.append(text.encode("utf-8", errors="ignore"))
        except Exception:
            pass
        return payloads

    def variants(gray):
        yield gray

        height, width = gray.shape[:2]
        max_side = max(height, width)
        if max_side > 720:
            scale = 720 / max_side
            small = cv2.resize(gray, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
            yield cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)

        yield cv2.medianBlur(gray, 3)

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        contrast = clahe.apply(gray)
        yield contrast

        blur = cv2.GaussianBlur(contrast, (0, 0), 1.2)
        sharpened = cv2.addWeighted(contrast, 1.6, blur, -0.6, 0)
        yield sharpened

        kernel = np.ones((2, 2), np.uint8)
        opened = cv2.morphologyEx(contrast, cv2.MORPH_OPEN, kernel)
        yield opened

    for image in images:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

        seen_shapes = set()
        for candidate in variants(gray):
            marker = (candidate.shape, int(candidate[0, 0]))
            if marker in seen_shapes:
                continue
            seen_shapes.add(marker)
            for data in decode_with_zxing(candidate):
                yield data
            for data in decode_with_opencv(candidate):
                yield data
            for result in decode(candidate, symbols=[ZBarSymbol.QRCODE]):
                yield result.data

            threshold = cv2.adaptiveThreshold(
                candidate,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                51,
                2,
            )
            for result in decode(threshold, symbols=[ZBarSymbol.QRCODE]):
                yield result.data
            for data in decode_with_zxing(threshold):
                yield data
            for data in decode_with_opencv(threshold):
                yield data


def frame_decode_images(frame, args):
    import cv2

    warped_images = list(red_frame_warps(frame, args))
    images = list(warped_images)

    if args.full_frame_fallback:
        resized = frame
        height, width = resized.shape[:2]
        max_side = max(height, width)
        if max_side > args.fallback_max_side:
            scale = args.fallback_max_side / max_side
            resized = cv2.resize(resized, (int(width * scale), int(height * scale)))
        images.append(resized)
    return images


def frame_decode_candidates(frame, args):
    yield from decode_qr_images(frame_decode_images(frame, args))


def binarize_for_vote(image, size: int):
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    resized = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(resized, (3, 3), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def voted_qr_image(binary_images):
    import numpy as np

    stack = np.stack(binary_images, axis=0)
    black_votes = np.sum(stack == 0, axis=0)
    voted = np.where(black_votes > (len(binary_images) / 2), 0, 255).astype("uint8")
    return voted


def decode_voted_buffer(binary_images):
    if not binary_images:
        return []
    return list(decode_qr_images([voted_qr_image(binary_images)]))


def record_chunk(db: dict[tuple[int, str], FileRecord], parsed, stats: DecodeStats) -> None:
    file_type, index, total, file_name, payload = parsed
    key = (file_type, file_name)
    if key not in db:
        db[key] = FileRecord(file_type=file_type, total=total)

    record = db[key]
    if record.total != total:
        raise ValueError("conflicting total for file")

    if index in record.chunks:
        stats.duplicate_chunks += 1
        return

    record.chunks[index] = payload
    stats.accepted_chunks += 1
    print(f"[OK] {file_name} chunk {index + 1}/{total}")


def maybe_record_confirmed_chunk(
    db: dict[tuple[int, str], FileRecord],
    parsed,
    stats: DecodeStats,
    pending: dict[tuple[int, str, int, int], int],
    confirm_copies: int,
) -> None:
    if confirm_copies <= 1:
        record_chunk(db, parsed, stats)
        return

    file_type, index, _total, file_name, payload = parsed
    payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    key = (file_type, file_name, index, payload_crc)
    pending[key] = pending.get(key, 0) + 1
    if pending[key] >= confirm_copies:
        record_chunk(db, parsed, stats)


def load_state(state_dir: Path) -> dict[tuple[int, str], FileRecord]:
    db: dict[tuple[int, str], FileRecord] = {}
    meta_path = state_dir / "state.json"
    chunks_root = state_dir / "chunks"
    if not meta_path.exists():
        return db

    data = json.loads(meta_path.read_text(encoding="utf-8"))
    for entry in data.get("files", []):
        file_type = int(entry["file_type"])
        file_name = safe_relative_name(str(entry["file_name"]))
        total = int(entry["total"])
        token = state_token(file_type, file_name)
        record = FileRecord(file_type=file_type, total=total)
        for index in entry.get("chunks", []):
            chunk_path = chunks_root / token / f"{int(index):08d}.bin"
            if chunk_path.exists():
                record.chunks[int(index)] = chunk_path.read_bytes()
        db[(file_type, file_name)] = record
    print(f"[INFO] Loaded resume state: {meta_path}")
    return db


def load_manifest_seed(manifest_json: str | None, db: dict[tuple[int, str], FileRecord]) -> None:
    if not manifest_json:
        return
    path = Path(manifest_json)
    if not path.exists():
        raise SystemExit(f"Manifest JSON not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("files", []):
        file_type = int(entry["file_type"])
        file_name = safe_relative_name(str(entry["file_name"]))
        total = int(entry["total"])
        key = (file_type, file_name)
        if key not in db:
            db[key] = FileRecord(file_type=file_type, total=total)
        elif db[key].total != total:
            raise SystemExit(f"Manifest conflicts with existing state for: {file_name}")
    print(f"[INFO] Loaded manifest seed: {path}")


def save_state(db: dict[tuple[int, str], FileRecord], state_dir: Path) -> None:
    chunks_root = state_dir / "chunks"
    chunks_root.mkdir(parents=True, exist_ok=True)

    files = []
    for (file_type, file_name), record in sorted(db.items(), key=lambda item: item[0]):
        token = state_token(file_type, file_name)
        item_dir = chunks_root / token
        item_dir.mkdir(parents=True, exist_ok=True)
        for index, payload in record.chunks.items():
            chunk_path = item_dir / f"{index:08d}.bin"
            if not chunk_path.exists():
                chunk_path.write_bytes(payload)
        files.append(
            {
                "file_type": file_type,
                "file_name": file_name,
                "total": record.total,
                "chunks": sorted(record.chunks),
            }
        )

    meta = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "protocol": "QVT1",
        "files": files,
    }
    (state_dir / "state.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] Saved resume state: {state_dir / 'state.json'}")


def write_missing_reports(db: dict[tuple[int, str], FileRecord], state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": [],
    }

    lines = [
        "# QR Video Decode Missing Report",
        "",
        f"- Generated at: {report['generated_at']}",
        "",
        "| Type | File | Received | Total | Missing count |",
        "|---|---|---:|---:|---:|",
    ]

    for (file_type, file_name), record in sorted(db.items(), key=lambda item: item[0]):
        missing = [index for index in range(record.total) if index not in record.chunks]
        type_label = "jar" if file_type == FILE_TYPE_JAR else "source"
        lines.append(f"| {type_label} | `{file_name}` | {len(record.chunks)} | {record.total} | {len(missing)} |")
        report["files"].append(
            {
                "file_type": file_type,
                "file_name": file_name,
                "total": record.total,
                "received_chunks": sorted(record.chunks),
                "missing_chunks": missing,
            }
        )

    for entry in report["files"]:
        type_label = "jar" if entry["file_type"] == FILE_TYPE_JAR else "source"
        missing = entry["missing_chunks"]
        lines.extend(
            [
                "",
                f"## {type_label}: `{entry['file_name']}`",
                "",
                f"- Received: `{len(entry['received_chunks'])}/{entry['total']}`",
                f"- Missing count: `{len(missing)}`",
                f"- Missing indexes: `{', '.join(str(index) for index in missing) if missing else 'none'}`",
            ]
        )

    json_path = state_dir / "missing_chunks.json"
    md_path = state_dir / "missing_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[INFO] Missing JSON for repair video: {json_path}")
    print(f"[INFO] Missing Markdown report: {md_path}")


def reconstruct_files(db: dict[tuple[int, str], FileRecord], output_dir: Path) -> int:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    failed = 0

    for (file_type, file_name), record in sorted(db.items(), key=lambda item: item[0]):
        received = len(record.chunks)
        if received != record.total:
            missing = [str(i + 1) for i in range(record.total) if i not in record.chunks]
            preview = ", ".join(missing[:30])
            suffix = " ..." if len(missing) > 30 else ""
            print(
                f"[FAIL] {file_name}: received {received}/{record.total}; "
                f"missing chunks: {preview}{suffix}"
            )
            failed += 1
            continue

        relative_name = Path(*safe_relative_name(file_name).split("/"))
        target_root = output_dir / "lib" if file_type == FILE_TYPE_JAR else output_dir
        target_path = (target_root / relative_name).resolve()
        resolved_root = target_root.resolve()
        if os.path.commonpath([str(resolved_root), str(target_path)]) != str(resolved_root):
            print(f"[FAIL] Unsafe output path rejected: {file_name}")
            failed += 1
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
        with tmp_path.open("wb") as out:
            for index in range(record.total):
                out.write(record.chunks[index])
        os.replace(tmp_path, target_path)
        print(f"[DONE] Reconstructed: {target_path}")

    return failed


def parse_record_region(raw_region: str) -> dict[str, int]:
    parts = [part.strip() for part in raw_region.split(",")]
    if len(parts) != 4:
        raise ValueError("record region must be left,top,width,height")
    left, top, width, height = (int(part) for part in parts)
    if width < 1 or height < 1:
        raise ValueError("record region width and height must be positive")
    return {"left": left, "top": top, "width": width, "height": height}


def default_record_output() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path("screen_recordings") / f"screen-record-{timestamp}.mp4"


def record_screen_video(args: argparse.Namespace) -> Path:
    try:
        import cv2
        import mss
        import numpy as np
    except ImportError as exc:
        raise SystemExit(
            "Missing screen recording dependency. Install with: pip install -r requirements-windows-decoder.txt"
        ) from exc

    output_path = Path(args.record_output) if args.record_output else default_record_output()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.record_fps <= 0:
        raise SystemExit("--record-fps must be positive")
    if args.record_seconds is not None and args.record_seconds <= 0:
        raise SystemExit("--record-seconds must be positive")

    with mss.MSS() as sct:
        if args.record_region:
            monitor = parse_record_region(args.record_region)
        else:
            if args.record_monitor < 0 or args.record_monitor >= len(sct.monitors):
                raise SystemExit(
                    f"--record-monitor must be between 0 and {len(sct.monitors) - 1}; "
                    "0 means all monitors."
                )
            monitor = dict(sct.monitors[args.record_monitor])

        width = int(monitor["width"])
        height = int(monitor["height"])
        fourcc = cv2.VideoWriter_fourcc(*(args.record_codec or "mp4v"))
        writer = cv2.VideoWriter(str(output_path), fourcc, args.record_fps, (width, height))
        if not writer.isOpened():
            raise SystemExit(f"Could not open screen recording writer for: {output_path}")

        frame_interval = 1.0 / args.record_fps
        start_time = time.perf_counter()
        next_frame_at = start_time
        frames = 0
        print(
            f"[INFO] Recording screen to: {output_path} "
            f"({width}x{height}, fps={args.record_fps:g})"
        )
        if args.record_seconds is None:
            print("[INFO] Press Ctrl+C to stop recording.")

        try:
            while True:
                now = time.perf_counter()
                elapsed = now - start_time
                if args.record_seconds is not None and elapsed >= args.record_seconds:
                    break
                if now < next_frame_at:
                    time.sleep(min(0.01, next_frame_at - now))
                    continue

                shot = sct.grab(monitor)
                frame = np.asarray(shot)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                writer.write(frame)
                frames += 1
                next_frame_at += frame_interval
        except KeyboardInterrupt:
            print("[INFO] Recording stopped by user.")
        finally:
            writer.release()

    duration = max(time.perf_counter() - start_time, 0.001)
    print(f"[DONE] Recording saved: {output_path.resolve()}")
    print(f"[DONE] Recorded frames: {frames}, duration={duration:.2f}s, effective_fps={frames / duration:.2f}")
    return output_path


def decode_video(args: argparse.Namespace) -> int:
    try:
        import cv2
        import numpy as np  # noqa: F401
        import pyzbar.pyzbar  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency. Install with: pip install -r requirements-windows-decoder.txt"
        ) from exc

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"Video not found: {video_path}", file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"Could not open video: {video_path}", file=sys.stderr)
        return 2

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    db = load_state(state_dir)
    load_manifest_seed(args.manifest_json, db)
    stats = DecodeStats()
    vote_buffer = []
    last_vote_binary = None
    pending_confirmations: dict[tuple[int, str, int, int], int] = {}

    print(f"[INFO] Decoding video: {video_path}")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        stats.video_frames += 1
        if args.every_n > 1 and (stats.video_frames - 1) % args.every_n != 0:
            continue
        if args.max_frames and stats.video_frames > args.max_frames:
            break

        candidate_images = frame_decode_images(frame, args)
        for raw_bytes in decode_qr_images(candidate_images):
            stats.qr_candidates += 1
            try:
                meta = parse_meta_payload(raw_bytes)
                if args.print_metadata:
                    print(
                        f"[META] {meta['file_name']} "
                        f"{meta['index'] + 1}/{meta['total']} crc=0x{meta['crc32']:08X}"
                    )
                continue
            except ValueError:
                pass
            try:
                parsed = parse_payload(raw_bytes)
                maybe_record_confirmed_chunk(
                    db,
                    parsed,
                    stats,
                    pending_confirmations,
                    args.confirm_copies,
                )
            except ValueError as exc:
                message = str(exc)
                if message == "crc mismatch":
                    stats.crc_failures += 1
                else:
                    stats.protocol_rejects += 1

        if args.vote_window > 1 and candidate_images:
            current_binary = binarize_for_vote(candidate_images[0], args.vote_size)
            if last_vote_binary is not None:
                import numpy as np

                diff_ratio = float(np.mean(current_binary != last_vote_binary))
                if diff_ratio > args.vote_change_threshold:
                    vote_buffer = []
            vote_buffer.append(current_binary)
            if len(vote_buffer) > args.vote_window:
                vote_buffer = vote_buffer[-args.vote_window :]
            last_vote_binary = current_binary

            if len(vote_buffer) >= args.vote_min_frames:
                for raw_bytes in decode_voted_buffer(vote_buffer):
                    stats.qr_candidates += 1
                    try:
                        meta = parse_meta_payload(raw_bytes)
                        if args.print_metadata:
                            print(
                                f"[META] {meta['file_name']} "
                                f"{meta['index'] + 1}/{meta['total']} crc=0x{meta['crc32']:08X}"
                            )
                        continue
                    except ValueError:
                        pass
                    try:
                        parsed = parse_payload(raw_bytes)
                        maybe_record_confirmed_chunk(
                            db,
                            parsed,
                            stats,
                            pending_confirmations,
                            args.confirm_copies,
                        )
                    except ValueError as exc:
                        message = str(exc)
                        if message == "crc mismatch":
                            stats.crc_failures += 1
                        else:
                            stats.protocol_rejects += 1
        elif args.vote_window > 1:
            vote_buffer = []
            last_vote_binary = None

        if args.progress_every and stats.video_frames % args.progress_every == 0:
            print(
                f"[INFO] frames={stats.video_frames}, accepted={stats.accepted_chunks}, "
                f"crc_fail={stats.crc_failures}, duplicates={stats.duplicate_chunks}"
            )

    cap.release()
    print(
        f"[INFO] Scan complete. frames={stats.video_frames}, qr={stats.qr_candidates}, "
        f"accepted={stats.accepted_chunks}, duplicates={stats.duplicate_chunks}, "
        f"crc_fail={stats.crc_failures}, rejected={stats.protocol_rejects}"
    )

    if not db:
        print("[FAIL] No valid protocol chunks were decoded.")
        return 1

    save_state(db, state_dir)
    write_missing_reports(db, state_dir)
    failed = reconstruct_files(db, Path(args.output))
    return 1 if failed else 0


def decode_fast_screen_video(args: argparse.Namespace) -> int:
    try:
        import cv2
        import zxingcpp
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency. Install with: pip install -r requirements-windows-decoder.txt"
        ) from exc

    video_path = Path(args.video)
    if not video_path.exists():
        print(f"Video not found: {video_path}", file=sys.stderr)
        return 2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"Could not open video: {video_path}", file=sys.stderr)
        return 2

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    db = load_state(state_dir)
    load_manifest_seed(args.manifest_json, db)
    stats = DecodeStats()
    pending_confirmations: dict[tuple[int, str, int, int], int] = {}

    print(f"[INFO] Fast screen decoding video: {video_path}")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        stats.video_frames += 1
        if args.max_frames and stats.video_frames > args.max_frames:
            break
        if args.every_n > 1 and (stats.video_frames - 1) % args.every_n != 0:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        results = []
        try:
            for pure in (False, True):
                results.extend(
                    zxingcpp.read_barcodes(
                        gray,
                        formats=zxingcpp.BarcodeFormat.QRCode,
                        try_rotate=False,
                        try_downscale=False,
                        try_invert=False,
                        is_pure=pure,
                        return_errors=False,
                    )
                )
        except Exception as exc:
            stats.protocol_rejects += 1
            if args.print_metadata:
                print(f"[WARN] zxing failed on frame {stats.video_frames}: {exc}")
            continue

        seen_raw_payloads = set()
        for result in results:
            raw_bytes = bytes(result.bytes) if result.bytes else result.text.encode("utf-8", errors="ignore")
            if not raw_bytes:
                continue
            if raw_bytes in seen_raw_payloads:
                continue
            seen_raw_payloads.add(raw_bytes)
            stats.qr_candidates += 1
            try:
                meta = parse_meta_payload(raw_bytes)
                if args.print_metadata:
                    print(
                        f"[META] {meta['file_name']} "
                        f"{meta['index'] + 1}/{meta['total']} crc=0x{meta['crc32']:08X}"
                    )
                continue
            except ValueError:
                pass
            try:
                parsed = parse_payload(raw_bytes)
                maybe_record_confirmed_chunk(
                    db,
                    parsed,
                    stats,
                    pending_confirmations,
                    args.confirm_copies,
                )
            except ValueError as exc:
                message = str(exc)
                if message == "crc mismatch":
                    stats.crc_failures += 1
                else:
                    stats.protocol_rejects += 1

        if args.progress_every and stats.video_frames % args.progress_every == 0:
            print(
                f"[INFO] frames={stats.video_frames}, accepted={stats.accepted_chunks}, "
                f"crc_fail={stats.crc_failures}, duplicates={stats.duplicate_chunks}"
            )

    cap.release()
    print(
        f"[INFO] Fast scan complete. frames={stats.video_frames}, qr={stats.qr_candidates}, "
        f"accepted={stats.accepted_chunks}, duplicates={stats.duplicate_chunks}, "
        f"crc_fail={stats.crc_failures}, rejected={stats.protocol_rejects}"
    )

    if not db:
        print("[FAIL] No valid protocol chunks were decoded.")
        return 1

    save_state(db, state_dir)
    write_missing_reports(db, state_dir)
    failed = reconstruct_files(db, Path(args.output))
    return 1 if failed else 0


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decode a phone-recorded red-framed QR transfer video."
    )
    parser.add_argument("video", nargs="?", help="Phone-recorded or screen-recorded video path.")
    parser.add_argument("-o", "--output", default=".\\reconstructed_project", help="Output directory.")
    parser.add_argument("--state-dir", default=".\\decode_state", help="Resume state directory. Reuse it across multiple videos.")
    parser.add_argument("--manifest-json", help="Encoder manifest JSON. Use it to report files with zero captured chunks.")
    parser.add_argument("--record-screen", action="store_true", help="Record the desktop to a video before decoding.")
    parser.add_argument("--record-only", action="store_true", help="Only record the desktop video; do not decode it.")
    parser.add_argument("--record-output", help="Screen recording output path. Defaults to screen_recordings/screen-record-<timestamp>.mp4.")
    parser.add_argument("--record-seconds", type=float, help="Screen recording duration. Omit to record until Ctrl+C.")
    parser.add_argument("--record-fps", type=float, default=12.0, help="Screen recording FPS.")
    parser.add_argument("--record-monitor", type=int, default=1, help="mss monitor index. 1 is usually the primary monitor; 0 records all monitors.")
    parser.add_argument("--record-region", help="Screen region to record as left,top,width,height.")
    parser.add_argument("--record-codec", default="mp4v", help="Screen recording FourCC codec, for example mp4v or XVID.")
    parser.add_argument("--screen-mode", action="store_true", help="Use faster defaults for direct video/screen recordings.")
    parser.add_argument("--screen-60fps", action="store_true", help="Shortcut for 60 FPS screen recordings where each QR is repeated 5 frames.")
    parser.add_argument("--screen-grid", action="store_true", help="Shortcut for multi-QR grid screen recordings. Uses fast full-frame decoding and every frame.")
    parser.add_argument("--fast-screen", dest="fast_screen", action="store_true", help="Use a fast full-frame zxing path for screen recordings.")
    parser.add_argument("--no-fast-screen", dest="fast_screen", action="store_false", help="Disable the fast screen-recording path.")
    parser.add_argument("--warp-size", type=int, default=960, help="Square size after perspective correction.")
    parser.add_argument("--crop-ratio", type=float, default=0.075, help="Crop ratio used to remove the red locator border.")
    parser.add_argument("--decode-padding", type=int, default=48, help="White padding added before QR decoding.")
    parser.add_argument("--min-red-area", type=float, default=5000.0, help="Minimum red contour area.")
    parser.add_argument("--max-contours", type=int, default=5, help="Maximum red contours to inspect per frame.")
    parser.add_argument("--red-saturation-min", type=int, default=80, help="Minimum HSV saturation for red mask.")
    parser.add_argument("--red-value-min", type=int, default=60, help="Minimum HSV value for red mask.")
    parser.add_argument("--every-n", type=int, default=1, help="Decode every Nth video frame.")
    parser.add_argument("--max-frames", type=int, help="Stop after this many video frames.")
    parser.add_argument("--progress-every", type=int, default=300, help="Progress print interval in video frames.")
    parser.add_argument("--fallback-max-side", type=int, default=1280, help="Max side length for full-frame fallback decoding.")
    parser.add_argument("--vote-window", type=int, default=5, help="Majority-vote consecutive frames for the same QR. Set 1 to disable.")
    parser.add_argument("--vote-min-frames", type=int, default=3, help="Minimum frames before trying a voted QR image.")
    parser.add_argument("--vote-size", type=int, default=900, help="Square size used for binary majority voting.")
    parser.add_argument("--vote-change-threshold", type=float, default=0.12, help="Reset voting buffer when binary frame difference is above this ratio.")
    parser.add_argument("--confirm-copies", type=int, default=1, help="Require this many identical decoded payload CRCs before accepting a chunk.")
    parser.add_argument("--print-metadata", action="store_true", help="Print top-left metadata QR hits.")
    parser.add_argument("--no-full-frame-fallback", dest="full_frame_fallback", action="store_false")
    parser.set_defaults(full_frame_fallback=True, fast_screen=None)
    args = parser.parse_args(list(argv))
    if args.screen_60fps:
        args.screen_mode = True
        args.record_fps = 60.0
        if args.every_n == 1:
            args.every_n = 5
        if args.vote_window == 5:
            args.vote_window = 1
    if args.screen_grid:
        args.screen_mode = True
        args.every_n = 1
        if args.vote_window == 5:
            args.vote_window = 1
    if args.screen_mode:
        if args.every_n == 1:
            args.every_n = 2
        if args.vote_window == 5:
            args.vote_window = 1
    if args.fast_screen is None:
        args.fast_screen = args.screen_mode
    if not args.video and not args.record_screen:
        parser.error("video is required unless --record-screen is used")
    if args.record_only and not args.record_screen:
        parser.error("--record-only requires --record-screen")
    return args


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    if args.record_screen:
        recorded_path = record_screen_video(args)
        if args.record_only:
            return 0
        args.video = str(recorded_path)
    if args.fast_screen:
        return decode_fast_screen_video(args)
    return decode_video(args)


if __name__ == "__main__":
    raise SystemExit(main())
