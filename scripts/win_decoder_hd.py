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
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import hashlib
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
FEC_MAGIC = b"QVF1"
PARAM_MAGIC = b"QVP1|"
PROTOCOL_VERSION = 1
FILE_TYPE_SOURCE = 0x01
FILE_TYPE_JAR = 0x02
HEADER_STRUCT = struct.Struct(">4sBBIIIHH")
HEADER_SIZE = HEADER_STRUCT.size
FEC_HEADER_STRUCT = struct.Struct(">4sBBIHIHHHHIH")
FEC_HEADER_SIZE = FEC_HEADER_STRUCT.size
MAX_FEC_SHARDS = 254
_ZXING_TRY_INVERT_SUPPORTED: bool | None = None


def zxing_read_barcodes(zxingcpp, image, *, try_invert: bool, **kwargs):
    global _ZXING_TRY_INVERT_SUPPORTED

    if _ZXING_TRY_INVERT_SUPPORTED is not False:
        try:
            results = zxingcpp.read_barcodes(image, try_invert=try_invert, **kwargs)
            _ZXING_TRY_INVERT_SUPPORTED = True
            return results
        except TypeError as exc:
            if "try_invert" not in str(exc):
                raise
            _ZXING_TRY_INVERT_SUPPORTED = False
    return zxingcpp.read_barcodes(image, **kwargs)


def _build_gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for index in range(255):
        exp[index] = value
        log[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        exp[index] = exp[index - 255]
    return exp, log


GF_EXP, GF_LOG = _build_gf_tables()


def gf_mul(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return GF_EXP[GF_LOG[left] + GF_LOG[right]]


def gf_div(left: int, right: int) -> int:
    if right == 0:
        raise ZeroDivisionError("GF division by zero")
    if left == 0:
        return 0
    return GF_EXP[(GF_LOG[left] - GF_LOG[right]) % 255]


def gf_pow(value: int, power: int) -> int:
    if power == 0:
        return 1
    if value == 0:
        return 0
    return GF_EXP[(GF_LOG[value] * power) % 255]


def gf_matrix_multiply(left: list[list[int]], right: list[list[int]]) -> list[list[int]]:
    rows = len(left)
    inner = len(right)
    cols = len(right[0]) if right else 0
    output = [[0] * cols for _ in range(rows)]
    for row in range(rows):
        for mid in range(inner):
            factor = left[row][mid]
            if factor == 0:
                continue
            for col in range(cols):
                output[row][col] ^= gf_mul(factor, right[mid][col])
    return output


def gf_matrix_inverse(matrix: list[list[int]]) -> list[list[int]]:
    size = len(matrix)
    work = [row[:] + [1 if row_index == col else 0 for col in range(size)] for row_index, row in enumerate(matrix)]
    for col in range(size):
        pivot = None
        for row in range(col, size):
            if work[row][col] != 0:
                pivot = row
                break
        if pivot is None:
            raise ValueError("FEC matrix is not invertible")
        if pivot != col:
            work[col], work[pivot] = work[pivot], work[col]
        pivot_value = work[col][col]
        if pivot_value != 1:
            inv_pivot = gf_div(1, pivot_value)
            work[col] = [gf_mul(value, inv_pivot) for value in work[col]]
        for row in range(size):
            if row == col:
                continue
            factor = work[row][col]
            if factor == 0:
                continue
            work[row] = [target ^ gf_mul(factor, source) for target, source in zip(work[row], work[col])]
    return [row[size:] for row in work]


def fec_generator_matrix(data_count: int, parity_count: int) -> list[list[int]]:
    total = data_count + parity_count
    if data_count < 1:
        raise ValueError("FEC data_count must be positive")
    if total > MAX_FEC_SHARDS:
        raise ValueError(f"FEC data + parity shards must be <= {MAX_FEC_SHARDS}")
    vandermonde = [
        [gf_pow(row + 1, col) for col in range(data_count)]
        for row in range(total)
    ]
    top_inverse = gf_matrix_inverse(vandermonde[:data_count])
    return gf_matrix_multiply(vandermonde, top_inverse)


def xor_scaled_into(target: bytearray, source: bytes, factor: int) -> None:
    if factor == 0:
        return
    if factor == 1:
        for index, value in enumerate(source):
            target[index] ^= value
        return
    for index, value in enumerate(source):
        target[index] ^= gf_mul(factor, value)


@dataclass
class FecShard:
    group_index: int
    group_start: int
    data_count: int
    parity_count: int
    shard_index: int
    payload: bytes


@dataclass
class FileRecord:
    file_type: int
    total: int
    chunks: dict[int, bytes] = field(default_factory=dict)
    chunk_sizes: dict[int, int] = field(default_factory=dict)
    file_size: int | None = None
    sha256: str | None = None
    fec_shards: dict[tuple[int, int], FecShard] = field(default_factory=dict)


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
    parameter_hits: int = 0
    accepted_chunks: int = 0
    accepted_fec_shards: int = 0
    fec_recovered_chunks: int = 0
    duplicate_chunks: int = 0
    duplicate_fec_shards: int = 0
    crc_failures: int = 0
    protocol_rejects: int = 0
    last_checkpoint_events: int = 0


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


def parse_parameter_payload(raw_bytes: bytes) -> dict:
    if not raw_bytes.startswith(PARAM_MAGIC):
        raise ValueError("not parameter payload")
    encoded = raw_bytes[len(PARAM_MAGIC) :].strip()
    try:
        padded = encoded + b"=" * (-len(encoded) % 4)
        compressed = base64.urlsafe_b64decode(padded)
        document = json.loads(zlib.decompress(compressed).decode("utf-8"))
    except Exception as exc:
        raise ValueError("bad parameter payload") from exc
    if document.get("magic") != "QVP1" or int(document.get("version", 0)) != 1:
        raise ValueError("bad parameter version")
    return document


def compact_parameter_summary(params: dict) -> str:
    video = params.get("video", {})
    qr = params.get("qr", {})
    fec = params.get("fec", {})
    return (
        f"{video.get('grid_cols', '?')}x{video.get('grid_rows', '?')}QR/"
        f"{video.get('fps', '?')}FPS/repeat={video.get('repeat', '?')}/"
        f"chunk={qr.get('chunk_size', '?')}/ECC={qr.get('error_correction', '?')}/"
        f"FEC={fec.get('group_size', 0)}+{fec.get('parity_chunks', 0)}"
    )


def apply_transfer_params(args: argparse.Namespace, params: dict) -> None:
    video = params.get("video", {})
    decoder = params.get("decoder", {})
    grid_cols = int(video.get("grid_cols") or 1)
    grid_rows = int(video.get("grid_rows") or 1)
    fps = float(video.get("fps") or args.record_fps)

    args.expected_qrs_per_frame = max(1, grid_cols * grid_rows)
    args.record_fps = fps
    lead_in_seconds = float(video.get("param_intro_seconds") or 0) + float(video.get("plain_intro_seconds") or 0)
    args.skip_lead_in_frames = max(0, int(fps * lead_in_seconds))
    if decoder.get("screen_grid", grid_cols * grid_rows > 1):
        args.screen_grid = True
        args.screen_mode = True
        args.every_n = int(decoder.get("every_n") or 1)
        if args.vote_window == 5:
            args.vote_window = int(decoder.get("vote_window") or 1)
    if decoder.get("fast_screen", grid_cols * grid_rows > 1):
        args.fast_screen = True
    if "memory_gb" in decoder and getattr(args, "memory_gb", None) is None:
        args.memory_gb = float(decoder["memory_gb"])
    if decoder.get("noise_robust", True):
        args.noise_robust = True


def remember_transfer_params(
    args: argparse.Namespace,
    params: dict,
    stats: DecodeStats | None = None,
    source: str = "video",
) -> bool:
    digest = hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    seen = getattr(args, "_seen_param_hashes", set())
    if digest in seen:
        if stats is not None:
            stats.parameter_hits += 1
        return False
    seen.add(digest)
    args._seen_param_hashes = seen
    args.transfer_params = params
    apply_transfer_params(args, params)
    if stats is not None:
        stats.parameter_hits += 1
    print(f"[PARAM] Loaded encoding parameters from {source}: {compact_parameter_summary(params)}")
    return True


def seed_db_from_parameter_header(params: dict | None, db: dict[tuple[int, str], FileRecord]) -> None:
    if not params:
        return
    qr = params.get("qr", {})
    chunk_size = int(qr.get("chunk_size") or 0)
    loaded = 0
    for entry in params.get("files", []) or []:
        file_type = int(entry["file_type"])
        file_name = safe_relative_name(str(entry["file_name"]))
        total = int(entry["total_chunks"])
        size = int(entry.get("size", 0))
        key = (file_type, file_name)
        if key not in db:
            db[key] = FileRecord(file_type=file_type, total=total)
        elif db[key].total != total:
            raise SystemExit(f"Parameter header conflicts with existing state for: {file_name}")
        record = db[key]
        record.file_size = size
        record.sha256 = entry.get("sha256", record.sha256)
        if chunk_size > 0:
            for index in range(total):
                if size == 0:
                    record.chunk_sizes[index] = 0
                elif index < total - 1:
                    record.chunk_sizes[index] = chunk_size
                else:
                    last_size = size - chunk_size * (total - 1)
                    record.chunk_sizes[index] = max(0, min(chunk_size, last_size))
        loaded += 1
    if loaded:
        print(f"[INFO] Loaded parameter header manifest seed: {loaded} files")


def decode_progress_counts(db: dict[tuple[int, str], FileRecord]) -> tuple[int, int]:
    total = sum(max(0, record.total) for record in db.values())
    done = sum(min(len(record.chunks), max(0, record.total)) for record in db.values())
    return done, total


def emit_decode_progress(db: dict[tuple[int, str], FileRecord], detail: str = "") -> None:
    done, total = decode_progress_counts(db)
    if total <= 0:
        return
    percent = done * 100 / total
    suffix = f" {detail}" if detail else ""
    print(f"[PROGRESS] decode {done}/{total} chunks {percent:.1f}%{suffix}", flush=True)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_bytes(payload)
    os.replace(tmp_path, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def persist_chunk_checkpoint(
    state_dir: Path,
    file_type: int,
    file_name: str,
    index: int,
    payload: bytes,
) -> None:
    token = state_token(file_type, file_name)
    chunk_path = state_dir / "chunks" / token / f"{index:08d}.bin"
    if not chunk_path.exists():
        atomic_write_bytes(chunk_path, payload)


def persist_fec_checkpoint(
    state_dir: Path,
    file_type: int,
    file_name: str,
    shard: FecShard,
) -> None:
    token = state_token(file_type, file_name)
    base_path = state_dir / "fec" / token / f"{shard.group_start:08d}_{shard.shard_index:03d}"
    payload_path = base_path.with_suffix(".bin")
    metadata_path = base_path.with_suffix(".json")
    if not payload_path.exists():
        atomic_write_bytes(payload_path, shard.payload)
    metadata = {
        "group_index": shard.group_index,
        "group_start": shard.group_start,
        "data_count": shard.data_count,
        "parity_count": shard.parity_count,
        "shard_index": shard.shard_index,
    }
    atomic_write_text(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2))


def parse_payload(raw_bytes: bytes):
    if raw_bytes.startswith(ASCII_MAGIC + b"|"):
        return parse_ascii_payload(raw_bytes)
    if raw_bytes.startswith(FEC_MAGIC):
        raise ValueError("fec payload")

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


def parse_fec_payload(raw_bytes: bytes):
    if len(raw_bytes) < FEC_HEADER_SIZE:
        raise ValueError("fec payload too short")
    (
        magic,
        version,
        file_type,
        total,
        group_index,
        group_start,
        data_count,
        parity_count,
        shard_index,
        payload_len,
        expected_crc,
        name_len,
    ) = FEC_HEADER_STRUCT.unpack_from(raw_bytes, 0)
    if magic != FEC_MAGIC:
        raise ValueError("bad fec magic")
    if version != PROTOCOL_VERSION:
        raise ValueError("bad fec protocol version")
    if file_type not in (FILE_TYPE_SOURCE, FILE_TYPE_JAR):
        raise ValueError("bad fec file type")
    if total < 1 or group_start >= total:
        raise ValueError("bad fec group start")
    if data_count < 1 or parity_count < 1 or data_count + parity_count > MAX_FEC_SHARDS:
        raise ValueError("bad fec group size")
    if shard_index < data_count or shard_index >= data_count + parity_count:
        raise ValueError("bad fec shard index")

    name_start = FEC_HEADER_SIZE
    name_end = name_start + name_len
    payload_start = name_end
    payload_end = payload_start + payload_len
    if payload_end != len(raw_bytes):
        raise ValueError("fec payload length mismatch")

    file_name = safe_relative_name(raw_bytes[name_start:name_end].decode("utf-8"))
    payload = raw_bytes[payload_start:payload_end]
    actual_crc = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ValueError("crc mismatch")

    return (
        file_type,
        total,
        file_name,
        FecShard(
            group_index=group_index,
            group_start=group_start,
            data_count=data_count,
            parity_count=parity_count,
            shard_index=shard_index,
            payload=payload,
        ),
    )


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
    try:
        from pyzbar.pyzbar import decode
        from pyzbar.wrapper import ZBarSymbol
    except Exception:
        decode = None
        ZBarSymbol = None

    def decode_with_zxing(candidate):
        try:
            import zxingcpp
        except ImportError:
            return []
        payloads = []
        for pure in (False, True):
            try:
                results = zxing_read_barcodes(
                    zxingcpp,
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

        _, otsu = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        yield otsu

        closed = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kernel)
        yield closed

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
            if decode is not None:
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
            if decode is not None:
                for result in decode(threshold, symbols=[ZBarSymbol.QRCODE]):
                    yield result.data
            for data in decode_with_zxing(threshold):
                yield data
            for data in decode_with_opencv(threshold):
                yield data


def scan_video_parameters(args: argparse.Namespace) -> dict | None:
    if not args.auto_params or not args.video:
        return None
    try:
        import cv2
    except ImportError:
        return None

    video_path = Path(args.video)
    if not video_path.exists():
        return None
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return None
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or args.record_fps or 30.0
        scan_frames = max(1, int(float(fps) * args.param_scan_seconds))
        sample_every = max(1, int(float(fps) / max(1.0, args.param_scan_hz)))
        frame_no = 0
        sampled = 0
        while frame_no < scan_frames:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            if (frame_no - 1) % sample_every != 0:
                continue
            sampled += 1
            for raw_bytes in decode_qr_images([frame]):
                try:
                    params = parse_parameter_payload(raw_bytes)
                    print(f"[INFO] Auto-parameter scan hit at frame {frame_no} after {sampled} sampled frames")
                    return params
                except ValueError:
                    continue
    finally:
        cap.release()
    print("[INFO] Auto-parameter scan did not find a QVP1 lead-in; using CLI/default decode parameters.")
    return None


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


def fast_screen_frame_payloads(frame, args) -> list[bytes]:
    import cv2
    import zxingcpp

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    results = []
    try:
        for pure in (False, True):
            results.extend(
                zxing_read_barcodes(
                    zxingcpp,
                    gray,
                    formats=zxingcpp.BarcodeFormat.QRCode,
                    try_rotate=bool(args.noise_robust),
                    try_downscale=bool(args.noise_robust),
                    try_invert=bool(args.noise_robust),
                    is_pure=pure,
                    return_errors=False,
                )
            )
    except Exception:
        if not args.noise_robust:
            raise

    payloads: list[bytes] = []
    seen_raw_payloads = set()
    for result in results:
        raw_bytes = bytes(result.bytes) if result.bytes else result.text.encode("utf-8", errors="ignore")
        if raw_bytes and raw_bytes not in seen_raw_payloads:
            seen_raw_payloads.add(raw_bytes)
            payloads.append(raw_bytes)

    expected_qrs = max(1, int(getattr(args, "expected_qrs_per_frame", 1)))
    has_parameter_header = any(payload.startswith(PARAM_MAGIC) for payload in payloads)
    if args.noise_robust and len(payloads) < expected_qrs and not has_parameter_header:
        for raw_bytes in decode_qr_images([frame]):
            if raw_bytes and raw_bytes not in seen_raw_payloads:
                seen_raw_payloads.add(raw_bytes)
                payloads.append(raw_bytes)
    return payloads


def max_pending_frames(args: argparse.Namespace, width: int, height: int) -> int:
    if args.max_pending_frames > 0:
        return args.max_pending_frames
    bytes_per_frame = max(1, width * height * 3)
    budget_bytes = int(args.memory_gb * 1024 * 1024 * 1024)
    # Decoding variants can temporarily multiply one frame several times.
    memory_bound = max(1, budget_bytes // (bytes_per_frame * 6))
    return max(1, min(args.workers * 4, memory_bound))


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


def record_chunk(
    db: dict[tuple[int, str], FileRecord],
    parsed,
    stats: DecodeStats,
    state_dir: Path | None = None,
) -> None:
    file_type, index, total, file_name, payload = parsed
    key = (file_type, file_name)
    created_record = key not in db
    if key not in db:
        db[key] = FileRecord(file_type=file_type, total=total)

    record = db[key]
    if record.total != total:
        raise ValueError("conflicting total for file")

    if index in record.chunks:
        stats.duplicate_chunks += 1
        return

    record.chunks[index] = payload
    record.chunk_sizes.setdefault(index, len(payload))
    if state_dir is not None:
        if created_record:
            save_state(db, state_dir, announce=False)
        persist_chunk_checkpoint(state_dir, file_type, file_name, index, payload)
    stats.accepted_chunks += 1
    maybe_save_resume_metadata(db, state_dir, stats)
    print(f"[OK] {file_name} chunk {index + 1}/{total}")
    emit_decode_progress(db, f"{file_name} {index + 1}/{total}")


def record_fec_shard(
    db: dict[tuple[int, str], FileRecord],
    parsed,
    stats: DecodeStats,
    state_dir: Path | None = None,
) -> None:
    file_type, total, file_name, shard = parsed
    key = (file_type, file_name)
    created_record = key not in db
    if key not in db:
        db[key] = FileRecord(file_type=file_type, total=total)

    record = db[key]
    if record.total != total:
        raise ValueError("conflicting total for fec file")

    shard_key = (shard.group_start, shard.shard_index)
    if shard_key in record.fec_shards:
        stats.duplicate_fec_shards += 1
        return

    record.fec_shards[shard_key] = shard
    if state_dir is not None:
        if created_record:
            save_state(db, state_dir, announce=False)
        persist_fec_checkpoint(state_dir, file_type, file_name, shard)
    stats.accepted_fec_shards += 1
    maybe_save_resume_metadata(db, state_dir, stats)
    print(
        f"[FEC] {file_name} group {shard.group_index + 1} "
        f"shard {shard.shard_index + 1}/{shard.data_count + shard.parity_count}"
    )


def maybe_record_confirmed_chunk(
    db: dict[tuple[int, str], FileRecord],
    parsed,
    stats: DecodeStats,
    pending: dict[tuple[int, str, int, int], int],
    confirm_copies: int,
    state_dir: Path | None = None,
) -> None:
    if confirm_copies <= 1:
        record_chunk(db, parsed, stats, state_dir)
        return

    file_type, index, _total, file_name, payload = parsed
    payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    key = (file_type, file_name, index, payload_crc)
    pending[key] = pending.get(key, 0) + 1
    if pending[key] >= confirm_copies:
        record_chunk(db, parsed, stats, state_dir)


def handle_decoded_payload(
    raw_bytes: bytes,
    db: dict[tuple[int, str], FileRecord],
    stats: DecodeStats,
    pending: dict[tuple[int, str, int, int], int],
    args: argparse.Namespace,
    source: str = "frame",
) -> None:
    try:
        params = parse_parameter_payload(raw_bytes)
        if remember_transfer_params(args, params, stats=stats, source=source):
            seed_db_from_parameter_header(params, db)
            emit_decode_progress(db, "parameter header")
        return
    except ValueError:
        pass

    try:
        meta = parse_meta_payload(raw_bytes)
        if args.print_metadata:
            print(
                f"[META] {meta['file_name']} "
                f"{meta['index'] + 1}/{meta['total']} crc=0x{meta['crc32']:08X}"
            )
        return
    except ValueError:
        pass

    if raw_bytes.startswith(FEC_MAGIC):
        try:
            parsed_fec = parse_fec_payload(raw_bytes)
            record_fec_shard(db, parsed_fec, stats, getattr(args, "_state_dir", None))
        except ValueError as exc:
            message = str(exc)
            if message == "crc mismatch":
                stats.crc_failures += 1
            else:
                stats.protocol_rejects += 1
        return

    try:
        parsed = parse_payload(raw_bytes)
        maybe_record_confirmed_chunk(
            db,
            parsed,
            stats,
            pending,
            args.confirm_copies,
            getattr(args, "_state_dir", None),
        )
    except ValueError as exc:
        message = str(exc)
        if message == "crc mismatch":
            stats.crc_failures += 1
        else:
            stats.protocol_rejects += 1


def load_state(state_dir: Path) -> dict[tuple[int, str], FileRecord]:
    db: dict[tuple[int, str], FileRecord] = {}
    meta_path = state_dir / "state.json"
    chunks_root = state_dir / "chunks"
    fec_root = state_dir / "fec"
    if not meta_path.exists():
        return db

    data = json.loads(meta_path.read_text(encoding="utf-8"))
    for entry in data.get("files", []):
        file_type = int(entry["file_type"])
        file_name = safe_relative_name(str(entry["file_name"]))
        total = int(entry["total"])
        token = state_token(file_type, file_name)
        record = FileRecord(
            file_type=file_type,
            total=total,
            file_size=entry.get("size"),
            sha256=entry.get("sha256"),
            chunk_sizes={int(index): int(size) for index, size in entry.get("chunk_sizes", {}).items()},
        )
        item_dir = chunks_root / token
        if item_dir.exists():
            for chunk_path in sorted(item_dir.glob("*.bin")):
                try:
                    index = int(chunk_path.stem)
                except ValueError:
                    continue
                if 0 <= index < total:
                    chunk_payload = chunk_path.read_bytes()
                    record.chunks[index] = chunk_payload
                    record.chunk_sizes.setdefault(index, len(chunk_payload))
        for shard_meta in entry.get("fec_shards", []):
            group_start = int(shard_meta["group_start"])
            shard_index = int(shard_meta["shard_index"])
            shard_path = fec_root / token / f"{group_start:08d}_{shard_index:03d}.bin"
            if shard_path.exists():
                shard = FecShard(
                    group_index=int(shard_meta["group_index"]),
                    group_start=group_start,
                    data_count=int(shard_meta["data_count"]),
                    parity_count=int(shard_meta["parity_count"]),
                    shard_index=shard_index,
                    payload=shard_path.read_bytes(),
                )
                record.fec_shards[(group_start, shard_index)] = shard
        fec_dir = fec_root / token
        if fec_dir.exists():
            for metadata_path in sorted(fec_dir.glob("*.json")):
                try:
                    shard_meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                    group_start = int(shard_meta["group_start"])
                    shard_index = int(shard_meta["shard_index"])
                    shard_path = fec_dir / f"{group_start:08d}_{shard_index:03d}.bin"
                    if not shard_path.exists():
                        continue
                    shard = FecShard(
                        group_index=int(shard_meta["group_index"]),
                        group_start=group_start,
                        data_count=int(shard_meta["data_count"]),
                        parity_count=int(shard_meta["parity_count"]),
                        shard_index=shard_index,
                        payload=shard_path.read_bytes(),
                    )
                    record.fec_shards[(group_start, shard_index)] = shard
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
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
        record = db[key]
        record.file_size = entry.get("size", record.file_size)
        record.sha256 = entry.get("sha256", record.sha256)
        for chunk_meta in entry.get("chunks", []):
            record.chunk_sizes[int(chunk_meta["index"])] = int(chunk_meta["size"])
    print(f"[INFO] Loaded manifest seed: {path}")


def save_state(
    db: dict[tuple[int, str], FileRecord],
    state_dir: Path,
    checkpoint: dict | None = None,
    announce: bool = True,
) -> None:
    chunks_root = state_dir / "chunks"
    fec_root = state_dir / "fec"
    chunks_root.mkdir(parents=True, exist_ok=True)
    fec_root.mkdir(parents=True, exist_ok=True)

    files = []
    for (file_type, file_name), record in sorted(db.items(), key=lambda item: item[0]):
        token = state_token(file_type, file_name)
        item_dir = chunks_root / token
        item_dir.mkdir(parents=True, exist_ok=True)
        for index, payload in record.chunks.items():
            chunk_path = item_dir / f"{index:08d}.bin"
            if not chunk_path.exists():
                chunk_path.write_bytes(payload)
        fec_dir = fec_root / token
        fec_dir.mkdir(parents=True, exist_ok=True)
        fec_meta = []
        for (_group_start, _shard_index), shard in sorted(record.fec_shards.items()):
            shard_path = fec_dir / f"{shard.group_start:08d}_{shard.shard_index:03d}.bin"
            if not shard_path.exists():
                shard_path.write_bytes(shard.payload)
            fec_meta.append(
                {
                    "group_index": shard.group_index,
                    "group_start": shard.group_start,
                    "data_count": shard.data_count,
                    "parity_count": shard.parity_count,
                    "shard_index": shard.shard_index,
                }
            )
        files.append(
            {
                "file_type": file_type,
                "file_name": file_name,
                "total": record.total,
                "size": record.file_size,
                "sha256": record.sha256,
                "chunk_sizes": {str(index): size for index, size in sorted(record.chunk_sizes.items())},
                "chunks": sorted(record.chunks),
                "fec_shards": fec_meta,
            }
        )

    meta = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "protocol": "QVT1",
        "files": files,
    }
    if checkpoint:
        meta["checkpoint"] = checkpoint
    atomic_write_text(state_dir / "state.json", json.dumps(meta, ensure_ascii=False, indent=2))
    if announce:
        print(f"[INFO] Saved resume state: {state_dir / 'state.json'}")


def maybe_save_resume_metadata(
    db: dict[tuple[int, str], FileRecord],
    state_dir: Path | None,
    stats: DecodeStats,
) -> None:
    if state_dir is None:
        return
    events = stats.accepted_chunks + stats.accepted_fec_shards
    if events == 1 or events - stats.last_checkpoint_events >= 25:
        save_state(db, state_dir, announce=False)
        stats.last_checkpoint_events = events


def load_decode_checkpoint(state_dir: Path, video_path: Path) -> int:
    meta_path = state_dir / "state.json"
    if not meta_path.exists():
        return 0
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        checkpoint = data.get("checkpoint") or {}
        expected = checkpoint.get("video") or {}
        stat = video_path.stat()
        if (
            expected.get("path") == str(video_path.resolve())
            and int(expected.get("size", -1)) == stat.st_size
            and int(expected.get("mtime_ns", -1)) == stat.st_mtime_ns
        ):
            return max(0, int(checkpoint.get("frame", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return 0


def decode_checkpoint(video_path: Path, frame_no: int) -> dict:
    stat = video_path.stat()
    return {
        "video": {
            "path": str(video_path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        },
        "frame": max(0, int(frame_no)),
    }


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


def recover_fec_group(
    db: dict[tuple[int, str], FileRecord],
    record: FileRecord,
    file_name: str,
    shard: FecShard,
) -> int:
    group_start = shard.group_start
    data_count = shard.data_count
    parity_count = shard.parity_count
    matrix = fec_generator_matrix(data_count, parity_count)
    selected_rows: list[int] = []
    selected_payloads: list[bytes] = []

    for local_index in range(data_count):
        global_index = group_start + local_index
        if global_index in record.chunks:
            selected_rows.append(local_index)
            selected_payloads.append(record.chunks[global_index])
    for candidate in sorted(record.fec_shards.values(), key=lambda item: item.shard_index):
        if (
            candidate.group_start == group_start
            and candidate.data_count == data_count
            and candidate.parity_count == parity_count
        ):
            selected_rows.append(candidate.shard_index)
            selected_payloads.append(candidate.payload)
        if len(selected_rows) >= data_count:
            break

    missing = [
        local_index
        for local_index in range(data_count)
        if group_start + local_index < record.total and group_start + local_index not in record.chunks
    ]
    if not missing:
        return 0
    if len(selected_rows) < data_count:
        return 0
    if any(group_start + local_index not in record.chunk_sizes for local_index in missing):
        print(f"[WARN] {file_name}: FEC group {shard.group_index + 1} needs manifest chunk sizes; skipped")
        return 0

    shard_size = max(len(payload) for payload in selected_payloads)
    selected_payloads = [payload.ljust(shard_size, b"\0") for payload in selected_payloads[:data_count]]
    selected_matrix = [matrix[row] for row in selected_rows[:data_count]]
    try:
        inverse = gf_matrix_inverse(selected_matrix)
    except ValueError:
        return 0

    recovered = 0
    for local_index in missing:
        output = bytearray(shard_size)
        for coeff, payload in zip(inverse[local_index], selected_payloads):
            xor_scaled_into(output, payload, coeff)
        global_index = group_start + local_index
        expected_size = record.chunk_sizes[global_index]
        record.chunks[global_index] = bytes(output[:expected_size])
        recovered += 1
        print(f"[FEC-OK] {file_name} chunk {global_index + 1}/{record.total} recovered")
        emit_decode_progress(db, f"{file_name} {global_index + 1}/{record.total} recovered")
    return recovered


def recover_missing_chunks_with_fec(db: dict[tuple[int, str], FileRecord], stats: DecodeStats) -> None:
    recovered_total = 0
    for (_file_type, file_name), record in sorted(db.items(), key=lambda item: item[0]):
        if not record.fec_shards:
            continue
        # Iterate until stable because recovering one chunk may make another group solvable.
        while True:
            before = len(record.chunks)
            seen_groups = set()
            for shard in sorted(record.fec_shards.values(), key=lambda item: (item.group_start, item.shard_index)):
                group_key = (shard.group_start, shard.data_count, shard.parity_count)
                if group_key in seen_groups:
                    continue
                seen_groups.add(group_key)
                recovered_total += recover_fec_group(db, record, file_name, shard)
            if len(record.chunks) == before:
                break
    stats.fec_recovered_chunks += recovered_total
    if recovered_total:
        print(f"[INFO] FEC recovered chunks: {recovered_total}")


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


def open_mss(mss_module):
    factory = getattr(mss_module, "mss", None) or getattr(mss_module, "MSS", None)
    if factory is None:
        raise SystemExit("The installed mss package does not expose a screen capture factory.")
    return factory()


def list_record_monitors() -> int:
    try:
        import mss
    except ImportError as exc:
        raise SystemExit(
            "Missing screen recording dependency. Install with: pip install -r requirements-windows-decoder.txt"
        ) from exc

    with open_mss(mss) as sct:
        monitors = []
        for index, monitor in enumerate(sct.monitors):
            monitors.append(
                {
                    "index": index,
                    "label": "All monitors" if index == 0 else f"Monitor {index}",
                    "left": int(monitor["left"]),
                    "top": int(monitor["top"]),
                    "width": int(monitor["width"]),
                    "height": int(monitor["height"]),
                }
            )
    print(f"[MONITORS] {json.dumps(monitors, ensure_ascii=True, separators=(',', ':'))}")
    return 0


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
    stop_file = Path(args.record_stop_file) if args.record_stop_file else None

    with open_mss(mss) as sct:
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
                if stop_file is not None and stop_file.exists():
                    print("[INFO] Recording stop requested by controller.")
                    break
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
    args._state_dir = state_dir
    db = load_state(state_dir)
    load_manifest_seed(args.manifest_json, db)
    seed_db_from_parameter_header(getattr(args, "transfer_params", None), db)
    emit_decode_progress(db, "ready")
    if db:
        save_state(db, state_dir, announce=False)
    stats = DecodeStats()
    resume_frame = load_decode_checkpoint(state_dir, video_path)
    if resume_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, resume_frame)
        stats.video_frames = resume_frame
        print(f"[RESUME] Continuing decode from video frame {resume_frame}")
    vote_buffer = []
    last_vote_binary = None
    pending_confirmations: dict[tuple[int, str, int, int], int] = {}

    print(f"[INFO] Decoding video: {video_path}")
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        stats.video_frames += 1
        if args.skip_lead_in_frames and stats.video_frames <= args.skip_lead_in_frames:
            continue
        if args.every_n > 1 and (stats.video_frames - 1) % args.every_n != 0:
            continue
        if args.max_frames and stats.video_frames > args.max_frames:
            break

        candidate_images = frame_decode_images(frame, args)
        for raw_bytes in decode_qr_images(candidate_images):
            stats.qr_candidates += 1
            handle_decoded_payload(raw_bytes, db, stats, pending_confirmations, args)

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
                    handle_decoded_payload(raw_bytes, db, stats, pending_confirmations, args, source="vote")
        elif args.vote_window > 1:
            vote_buffer = []
            last_vote_binary = None

        if args.progress_every and stats.video_frames % args.progress_every == 0:
            print(
                f"[INFO] frames={stats.video_frames}, accepted={stats.accepted_chunks}, "
                f"fec={stats.accepted_fec_shards}, params={stats.parameter_hits}, "
                f"crc_fail={stats.crc_failures}, duplicates={stats.duplicate_chunks}"
            )
            save_state(db, state_dir, decode_checkpoint(video_path, stats.video_frames))

    cap.release()
    print(
        f"[INFO] Scan complete. frames={stats.video_frames}, qr={stats.qr_candidates}, "
        f"accepted={stats.accepted_chunks}, fec={stats.accepted_fec_shards}, "
        f"params={stats.parameter_hits}, duplicates={stats.duplicate_chunks}, "
        f"crc_fail={stats.crc_failures}, rejected={stats.protocol_rejects}"
    )

    if not db:
        print("[FAIL] No valid protocol chunks were decoded.")
        return 1

    recover_missing_chunks_with_fec(db, stats)
    save_state(db, state_dir, decode_checkpoint(video_path, stats.video_frames))
    write_missing_reports(db, state_dir)
    failed = reconstruct_files(db, Path(args.output))
    return 1 if failed else 0


def decode_fast_screen_video(args: argparse.Namespace) -> int:
    try:
        import cv2
        import zxingcpp  # noqa: F401
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
    args._state_dir = state_dir
    db = load_state(state_dir)
    load_manifest_seed(args.manifest_json, db)
    seed_db_from_parameter_header(getattr(args, "transfer_params", None), db)
    emit_decode_progress(db, "ready")
    if db:
        save_state(db, state_dir, announce=False)
    stats = DecodeStats()
    resume_frame = load_decode_checkpoint(state_dir, video_path)
    if resume_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, resume_frame)
        stats.video_frames = resume_frame
        print(f"[RESUME] Continuing decode from video frame {resume_frame}")
    pending_confirmations: dict[tuple[int, str, int, int], int] = {}

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    pending_limit = max_pending_frames(args, width, height)
    print(
        f"[INFO] Fast screen decoding video: {video_path} "
        f"(workers={args.workers}, memory={args.memory_gb:g}GB, pending<={pending_limit}, noise_robust={args.noise_robust})"
    )

    executor = ThreadPoolExecutor(max_workers=args.workers) if args.workers > 1 else None
    pending: dict[Future, int] = {}

    def process_payloads(frame_no: int, payloads: list[bytes]) -> None:
        for raw_bytes in payloads:
            stats.qr_candidates += 1
            handle_decoded_payload(raw_bytes, db, stats, pending_confirmations, args, source=f"frame {frame_no}")

    def drain_completed(wait_for_all: bool = False) -> None:
        while pending and (wait_for_all or len(pending) >= pending_limit):
            done, _not_done = wait(
                pending.keys(),
                return_when=FIRST_COMPLETED if not wait_for_all else FIRST_COMPLETED,
            )
            for future in done:
                frame_no = pending.pop(future)
                try:
                    process_payloads(frame_no, future.result())
                except Exception as exc:
                    stats.protocol_rejects += 1
                    if args.print_metadata:
                        print(f"[WARN] decode worker failed on frame {frame_no}: {exc}")
            if not wait_for_all:
                break

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            stats.video_frames += 1
            frame_no = stats.video_frames
            if args.skip_lead_in_frames and frame_no <= args.skip_lead_in_frames:
                continue
            if args.max_frames and frame_no > args.max_frames:
                break
            if args.every_n > 1 and (frame_no - 1) % args.every_n != 0:
                continue

            if executor is None:
                process_payloads(frame_no, fast_screen_frame_payloads(frame, args))
            else:
                pending[executor.submit(fast_screen_frame_payloads, frame, args)] = frame_no
                drain_completed(wait_for_all=False)

            if args.progress_every and stats.video_frames % args.progress_every == 0:
                print(
                    f"[INFO] frames={stats.video_frames}, accepted={stats.accepted_chunks}, "
                    f"fec={stats.accepted_fec_shards}, params={stats.parameter_hits}, "
                    f"crc_fail={stats.crc_failures}, duplicates={stats.duplicate_chunks}, pending={len(pending)}"
                )
                checkpoint_frame = min(pending.values()) - 1 if pending else stats.video_frames
                save_state(db, state_dir, decode_checkpoint(video_path, checkpoint_frame))
        drain_completed(wait_for_all=True)
    finally:
        cap.release()
        if executor is not None:
            executor.shutdown(wait=True)

    print(
        f"[INFO] Fast scan complete. frames={stats.video_frames}, qr={stats.qr_candidates}, "
        f"accepted={stats.accepted_chunks}, fec={stats.accepted_fec_shards}, "
        f"params={stats.parameter_hits}, duplicates={stats.duplicate_chunks}, "
        f"crc_fail={stats.crc_failures}, rejected={stats.protocol_rejects}"
    )

    if not db:
        print("[FAIL] No valid protocol chunks were decoded.")
        return 1

    recover_missing_chunks_with_fec(db, stats)
    save_state(db, state_dir, decode_checkpoint(video_path, stats.video_frames))
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
    parser.add_argument("--record-stop-file", help="Stop recording cleanly when this marker file appears.")
    parser.add_argument("--list-record-monitors", action="store_true", help="Print the mss monitor list as JSON and exit.")
    parser.add_argument("--screen-mode", action="store_true", help="Use faster defaults for direct video/screen recordings.")
    parser.add_argument("--screen-60fps", action="store_true", help="Shortcut for 60 FPS screen recordings where each QR is repeated 5 frames.")
    parser.add_argument("--screen-grid", action="store_true", help="Shortcut for multi-QR grid screen recordings. Uses fast full-frame decoding and every frame.")
    parser.add_argument("--screen-fast-fec-4qr", action="store_true", help="Shortcut for 4QR/30FPS/repeat=2/FEC videos.")
    parser.add_argument("--fast-screen", dest="fast_screen", action="store_true", help="Use a fast full-frame zxing path for screen recordings.")
    parser.add_argument("--no-fast-screen", dest="fast_screen", action="store_false", help="Disable the fast screen-recording path.")
    parser.add_argument("--auto-params", dest="auto_params", action="store_true", help="Scan the QVP1 lead-in QR and auto-match decode parameters.")
    parser.add_argument("--no-auto-params", dest="auto_params", action="store_false", help="Disable QVP1 lead-in auto parameter matching.")
    parser.add_argument("--param-scan-seconds", type=float, default=12.0, help="Seconds from the start of the video to scan for QVP1 parameters.")
    parser.add_argument("--param-scan-hz", type=float, default=5.0, help="Sampling rate used while scanning the QVP1 lead-in.")
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
    parser.add_argument("--workers", type=int, default=0, help="Decode workers. 0 auto-selects up to 6 workers.")
    parser.add_argument("--memory-gb", type=float, default=6.0, help="Decode memory budget used to bound queued frames.")
    parser.add_argument("--max-pending-frames", type=int, default=0, help="Max queued decode frames. 0 derives from --memory-gb and --workers.")
    parser.add_argument("--noise-robust", dest="noise_robust", action="store_true", help="Use slower contrast/threshold fallback variants when fast zxing misses QR codes.")
    parser.add_argument("--no-noise-robust", dest="noise_robust", action="store_false", help="Disable noise-robust fallback variants.")
    parser.add_argument("--print-metadata", action="store_true", help="Print top-left metadata QR hits.")
    parser.add_argument("--no-full-frame-fallback", dest="full_frame_fallback", action="store_false")
    parser.set_defaults(full_frame_fallback=True, fast_screen=None, auto_params=True, noise_robust=True)
    args = parser.parse_args(list(argv))
    if args.screen_60fps:
        args.screen_mode = True
        args.record_fps = 60.0
        if args.every_n == 1:
            args.every_n = 5
        if args.vote_window == 5:
            args.vote_window = 1
    if args.screen_fast_fec_4qr:
        args.screen_grid = True
        args.record_fps = 30.0
        args.every_n = 1
        if args.vote_window == 5:
            args.vote_window = 1
    if args.screen_grid:
        args.screen_mode = True
        args.every_n = 1
        if args.vote_window == 5:
            args.vote_window = 1
    if args.screen_mode:
        if args.every_n == 1 and not args.screen_grid:
            args.every_n = 2
        if args.vote_window == 5:
            args.vote_window = 1
    if args.fast_screen is None:
        args.fast_screen = args.screen_mode
    if args.workers < 0:
        parser.error("--workers must be zero or positive")
    if args.workers == 0:
        args.workers = max(1, min(6, os.cpu_count() or 1))
    if args.memory_gb <= 0:
        parser.error("--memory-gb must be positive")
    if args.max_pending_frames < 0:
        parser.error("--max-pending-frames must be zero or positive")
    if args.param_scan_seconds < 0:
        parser.error("--param-scan-seconds must not be negative")
    if args.param_scan_hz <= 0:
        parser.error("--param-scan-hz must be positive")
    args.expected_qrs_per_frame = 4 if args.screen_fast_fec_4qr else 1
    args.transfer_params = None
    args.skip_lead_in_frames = 0
    if not args.video and not args.record_screen and not args.list_record_monitors:
        parser.error("video is required unless --record-screen is used")
    if args.record_only and not args.record_screen:
        parser.error("--record-only requires --record-screen")
    return args


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    if args.list_record_monitors:
        return list_record_monitors()
    if args.record_screen:
        recorded_path = record_screen_video(args)
        if args.record_only:
            return 0
        args.video = str(recorded_path)
    params = scan_video_parameters(args)
    if params:
        remember_transfer_params(args, params, source="intro scan")
    if args.fast_screen:
        return decode_fast_screen_video(args)
    return decode_video(args)


if __name__ == "__main__":
    raise SystemExit(main())
