#!/usr/bin/env python3
"""
Generate a QR-code video for optical file transfer.

Sender side: Linux/Python. The output video is intended to be played on the
source machine and recorded by a phone. The Windows decoder script can recover
the files from the phone recording.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import Executor, ThreadPoolExecutor
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


MAGIC = b"QVT1"
ASCII_MAGIC = b"QVT2"
FEC_MAGIC = b"QVF1"
PARAM_MAGIC = b"QVP1|"
PROTOCOL_VERSION = 1
FILE_TYPE_SOURCE = 0x01
SUPPORTED_ARCHIVE_SUFFIXES = (
    ".zip",
    ".7z",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz2",
    ".tar.xz",
    ".txz",
    ".npm",
)
HEADER_STRUCT = struct.Struct(">4sBBIIIHH")
HEADER_SIZE = HEADER_STRUCT.size
FEC_HEADER_STRUCT = struct.Struct(">4sBBIHIHHHHIH")
FEC_HEADER_SIZE = FEC_HEADER_STRUCT.size
MAX_FEC_SHARDS = 254


@dataclass(frozen=True)
class TransferFile:
    path: Path
    file_type: int
    transfer_name: str


@dataclass(frozen=True)
class ChunkMeta:
    index: int
    size: int
    crc32: int
    sha256: str


@dataclass(frozen=True)
class FileManifest:
    item: TransferFile
    size: int
    sha256: str
    total_chunks: int
    chunks: list[ChunkMeta]


@dataclass(frozen=True)
class FramePacket:
    payload: bytes
    label: str
    meta_payload: bytes | None


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


def fec_parity_shards(chunks: list[bytes], shard_size: int, parity_count: int) -> list[bytes]:
    data_count = len(chunks)
    matrix = fec_generator_matrix(data_count, parity_count)
    padded = [chunk.ljust(shard_size, b"\0") for chunk in chunks]
    parities: list[bytes] = []
    for row in matrix[data_count:]:
        parity = bytearray(shard_size)
        for coeff, chunk in zip(row, padded):
            xor_scaled_into(parity, chunk, coeff)
        parities.append(bytes(parity))
    return parities


def manifest_key(file_type: int, transfer_name: str) -> str:
    return f"{file_type}:{transfer_name}"


def normalize_transfer_name(name: str) -> str:
    normalized = name.replace("\\", "/").strip("/")
    parts = []
    for part in normalized.split("/"):
        if not part or part in (".", ".."):
            continue
        parts.append(part)
    if not parts:
        raise ValueError(f"Invalid transfer name: {name!r}")
    return "/".join(parts)


def is_supported_archive(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES)


def collect_inputs(args: argparse.Namespace) -> list[TransferFile]:
    items: list[TransferFile] = []

    for raw in [*(args.source or []), *args.inputs]:
        path = Path(raw)
        if path.is_dir():
            raise SystemExit(f"Directories are not supported. Compress the directory first: {path}")

        if not path.is_file():
            raise SystemExit(f"Archive not found: {path}")

        if not is_supported_archive(path):
            supported = ", ".join(SUPPORTED_ARCHIVE_SUFFIXES[:-1])
            raise SystemExit(f"Only compressed archives are supported ({supported}): {path}")
        items.append(TransferFile(path, FILE_TYPE_SOURCE, normalize_transfer_name(path.name)))

    dedup: dict[tuple[int, str], TransferFile] = {}
    for item in items:
        key = (item.file_type, item.transfer_name)
        if key in dedup:
            print(f"[WARN] Duplicate transfer target skipped: {item.transfer_name}", file=sys.stderr)
            continue
        dedup[key] = item

    return list(dedup.values())


def iter_chunks(path: Path, chunk_size: int) -> Iterator[bytes]:
    yielded = False
    with path.open("rb") as file_obj:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            yielded = True
            yield chunk
    if not yielded:
        yield b""


def chunk_count(path: Path, chunk_size: int) -> int:
    size = path.stat().st_size
    return max(1, math.ceil(size / chunk_size))


def build_manifest(items: list[TransferFile], chunk_size: int) -> list[FileManifest]:
    manifests: list[FileManifest] = []
    for item in items:
        file_hash = hashlib.sha256()
        chunks: list[ChunkMeta] = []
        for index, chunk in enumerate(iter_chunks(item.path, chunk_size)):
            file_hash.update(chunk)
            chunks.append(
                ChunkMeta(
                    index=index,
                    size=len(chunk),
                    crc32=zlib.crc32(chunk) & 0xFFFFFFFF,
                    sha256=hashlib.sha256(chunk).hexdigest(),
                )
            )
        if not chunks:
            empty_hash = hashlib.sha256(b"").hexdigest()
            chunks.append(ChunkMeta(index=0, size=0, crc32=0, sha256=empty_hash))
        manifests.append(
            FileManifest(
                item=item,
                size=item.path.stat().st_size,
                sha256=file_hash.hexdigest(),
                total_chunks=len(chunks),
                chunks=chunks,
            )
        )
    return manifests


def load_resume_filter(path: str | None) -> dict[tuple[int, str], set[int]] | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    files = data.get("files", data if isinstance(data, list) else [])
    wanted: dict[tuple[int, str], set[int]] = {}
    for entry in files:
        file_type = int(entry["file_type"])
        file_name = normalize_transfer_name(str(entry["file_name"]))
        chunks = {int(index) for index in entry.get("missing_chunks", [])}
        if chunks:
            wanted[(file_type, file_name)] = chunks
    return wanted


def selected_chunk_indexes(
    manifest: FileManifest,
    resume_filter: dict[tuple[int, str], set[int]] | None,
    preview_chunks: int | None = None,
) -> set[int]:
    if resume_filter is None:
        selected = {chunk.index for chunk in manifest.chunks}
    else:
        selected = set(resume_filter.get((manifest.item.file_type, manifest.item.transfer_name), set()))
    if preview_chunks is not None:
        selected = {index for index in selected if index < preview_chunks}
    return selected


def fec_enabled(args: argparse.Namespace) -> bool:
    return args.fec_parity_chunks > 0


def selected_fec_group_indexes(
    manifest: FileManifest,
    resume_filter: dict[tuple[int, str], set[int]] | None,
    preview_chunks: int | None,
    args: argparse.Namespace,
) -> set[int]:
    if not fec_enabled(args):
        return set()
    selected = selected_chunk_indexes(manifest, resume_filter, preview_chunks)
    if resume_filter is None and preview_chunks is None:
        return set(range(math.ceil(manifest.total_chunks / args.fec_group_size)))
    return {index // args.fec_group_size for index in selected if 0 <= index < manifest.total_chunks}


def count_selected_fec_packets(
    manifest: FileManifest,
    resume_filter: dict[tuple[int, str], set[int]] | None,
    preview_chunks: int | None,
    args: argparse.Namespace,
) -> int:
    return len(selected_fec_group_indexes(manifest, resume_filter, preview_chunks, args)) * args.fec_parity_chunks


def write_manifest_md(manifests: list[FileManifest], args: argparse.Namespace, resume_filter) -> Path:
    manifest_path = Path(args.manifest) if args.manifest else Path(args.output).with_suffix(".manifest.md")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    total_files = len(manifests)
    total_chunks = sum(len(manifest.chunks) for manifest in manifests)
    selected_chunks = sum(len(selected_chunk_indexes(manifest, resume_filter, args.preview_chunks)) for manifest in manifests)

    lines = [
        "# QR Video Chunk Manifest",
        "",
        f"- Generated at: {datetime.now().isoformat(timespec='seconds')}",
        f"- Output video: `{args.output}`",
        f"- Chunk size: `{args.chunk_size}` bytes",
        f"- FEC: `{'enabled' if fec_enabled(args) else 'disabled'}`",
        f"- FEC group size: `{args.fec_group_size}` data chunks",
        f"- FEC parity chunks: `{args.fec_parity_chunks}` per group",
        f"- Files: `{total_files}`",
        f"- Total logical chunks: `{total_chunks}`",
        f"- Chunks encoded in this run: `{selected_chunks}`",
        f"- Resume filter: `{args.resume_missing or 'none'}`",
        "",
        "## Files",
        "",
        "| Type | Transfer name | Size | SHA256 | Chunks | Encoded chunks |",
        "|---|---:|---:|---|---:|---:|",
    ]

    for manifest in manifests:
        type_label = "archive"
        encoded = len(selected_chunk_indexes(manifest, resume_filter, args.preview_chunks))
        fec_packets = count_selected_fec_packets(manifest, resume_filter, args.preview_chunks, args)
        lines.append(
            f"| {type_label} | `{manifest.item.transfer_name}` | {manifest.size} | "
            f"`{manifest.sha256}` | {manifest.total_chunks} | {encoded}"
            f"{f' + {fec_packets} FEC' if fec_packets else ''} |"
        )

    for manifest in manifests:
        type_label = "archive"
        lines.extend(
            [
                "",
                f"## {type_label}: `{manifest.item.transfer_name}`",
                "",
                f"- File type code: `{manifest.item.file_type}`",
                f"- Source path: `{manifest.item.path}`",
                f"- File size: `{manifest.size}`",
                f"- File SHA256: `{manifest.sha256}`",
                f"- Total chunks: `{manifest.total_chunks}`",
                f"- FEC groups encoded: `{len(selected_fec_group_indexes(manifest, resume_filter, args.preview_chunks, args))}`",
                "",
                "| Index | Size | CRC32 | Chunk SHA256 | Encoded in this run |",
                "|---:|---:|---|---|---|",
            ]
        )
        selected = selected_chunk_indexes(manifest, resume_filter, args.preview_chunks)
        for chunk in manifest.chunks:
            encoded = "yes" if chunk.index in selected else "no"
            lines.append(
                f"| {chunk.index} | {chunk.size} | `0x{chunk.crc32:08X}` | "
                f"`{chunk.sha256}` | {encoded} |"
            )

    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[DONE] Chunk manifest written: {manifest_path.resolve()}")
    return manifest_path


def write_manifest_json(manifests: list[FileManifest], args: argparse.Namespace, resume_filter) -> Path:
    json_path = Path(args.manifest_json) if args.manifest_json else Path(args.output).with_suffix(".manifest.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "protocol": "QVT1",
        "output_video": args.output,
        "chunk_size": args.chunk_size,
        "payload_mode": args.payload_mode,
        "qr_error_correction": args.qr_error_correction,
        "grid_cols": args.grid_cols,
        "grid_rows": args.grid_rows,
        "param_intro_seconds": args.param_intro_seconds,
        "workers": args.workers,
        "memory_gb": args.memory_gb,
        "fec": {
            "enabled": fec_enabled(args),
            "group_size": args.fec_group_size,
            "parity_chunks": args.fec_parity_chunks,
        },
        "resume_filter": args.resume_missing,
        "files": [],
    }
    for manifest in manifests:
        selected = selected_chunk_indexes(manifest, resume_filter, args.preview_chunks)
        selected_fec_groups = selected_fec_group_indexes(manifest, resume_filter, args.preview_chunks, args)
        data["files"].append(
            {
                "file_type": manifest.item.file_type,
                "file_name": manifest.item.transfer_name,
                "source_path": str(manifest.item.path),
                "size": manifest.size,
                "sha256": manifest.sha256,
                "total": manifest.total_chunks,
                "encoded_chunks": sorted(selected),
                "encoded_fec_groups": sorted(selected_fec_groups),
                "chunks": [
                    {
                        "index": chunk.index,
                        "size": chunk.size,
                        "crc32": f"0x{chunk.crc32:08X}",
                        "sha256": chunk.sha256,
                    }
                    for chunk in manifest.chunks
                ],
            }
        )
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[DONE] Machine manifest written: {json_path.resolve()}")
    return json_path


def build_parameter_document(
    manifests: list[FileManifest],
    args: argparse.Namespace,
    include_files: bool = True,
) -> dict:
    """Build the self-describing video header shown during the QR lead-in."""

    width, height = frame_size(args)
    document = {
        "magic": "QVP1",
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "video": {
            "fps": args.fps,
            "repeat": args.repeat,
            "passes": args.passes,
            "width": width,
            "height": height,
            "grid_cols": args.grid_cols,
            "grid_rows": args.grid_rows,
            "grid_gap": args.grid_gap,
            "grid_margin": args.grid_margin,
            "codec": args.codec or "",
            "param_intro_seconds": args.param_intro_seconds,
            "plain_intro_seconds": args.intro_seconds,
            "outro_seconds": args.outro_seconds,
        },
        "qr": {
            "payload_mode": args.payload_mode,
            "version": args.qr_version,
            "error_correction": args.qr_error_correction,
            "chunk_size": args.chunk_size,
            "box_size": args.box_size,
            "border": args.qr_border,
            "color_border": args.color_border,
            "outer_white": args.outer_white,
            "label_height": args.label_height,
            "meta_qr_size": args.meta_qr_size,
            "meta_qr_version": args.meta_qr_version,
        },
        "fec": {
            "enabled": fec_enabled(args),
            "group_size": args.fec_group_size,
            "parity_chunks": args.fec_parity_chunks,
        },
        "decoder": {
            "fast_screen": args.grid_cols * args.grid_rows > 1,
            "screen_grid": args.grid_cols * args.grid_rows > 1,
            "every_n": 1,
            "vote_window": 1 if args.grid_cols * args.grid_rows > 1 else 5,
            "memory_gb": args.memory_gb,
            "workers": args.workers,
            "noise_robust": True,
        },
    }
    if include_files:
        document["files"] = [
            {
                "file_type": manifest.item.file_type,
                "file_name": manifest.item.transfer_name,
                "size": manifest.size,
                "sha256": manifest.sha256,
                "total_chunks": manifest.total_chunks,
            }
            for manifest in manifests
        ]
    return document


def build_parameter_payload(
    manifests: list[FileManifest],
    args: argparse.Namespace,
    include_files: bool = True,
) -> bytes:
    document = build_parameter_document(manifests, args, include_files=include_files)
    raw_json = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(raw_json, 9)
    encoded = base64.urlsafe_b64encode(compressed).rstrip(b"=")
    return PARAM_MAGIC + encoded


def render_parameter_intro_frame(
    manifests: list[FileManifest],
    args: argparse.Namespace,
    executor: Executor | None = None,
):
    capacity = args.grid_cols * args.grid_rows
    last_error: Exception | None = None
    for include_files in (True, False):
        payload = build_parameter_payload(manifests, args, include_files=include_files)
        label = "PARAMS AUTO" if include_files else "PARAMS AUTO NOFILES"
        packets = [FramePacket(payload=payload, label=label, meta_payload=None) for _ in range(capacity)]
        try:
            frame = make_grid_frame(packets, args, executor=executor)
            if not include_files:
                print(
                    "[WARN] Parameter QR could not fit the file manifest; "
                    "decoder will still auto-match video parameters, but FEC recovery may need --manifest-json."
                )
            print(
                f"[INFO] Parameter QR lead-in payload: {len(payload)} bytes "
                f"({'with' if include_files else 'without'} file manifest)"
            )
            return frame
        except Exception as exc:
            last_error = exc
            if include_files:
                continue
            raise
    raise RuntimeError("Could not render parameter intro frame") from last_error


def build_meta_payload(
    item: TransferFile,
    index: int,
    total: int,
    chunk: bytes,
) -> bytes:
    name_bytes = item.transfer_name.encode("utf-8")
    crc32 = zlib.crc32(chunk) & 0xFFFFFFFF
    return b"|".join(
        [
            b"QVM1",
            str(PROTOCOL_VERSION).encode("ascii"),
            str(item.file_type).encode("ascii"),
            str(index).encode("ascii"),
            str(total).encode("ascii"),
            f"{crc32:08X}".encode("ascii"),
            str(len(chunk)).encode("ascii"),
            base64.b64encode(name_bytes),
        ]
    )


def build_frame_payload(
    item: TransferFile,
    index: int,
    total: int,
    chunk: bytes,
    payload_mode: str = "ascii",
) -> bytes:
    name_bytes = item.transfer_name.encode("utf-8")
    if len(name_bytes) > 65535:
        raise ValueError(f"File name is too long for protocol: {item.transfer_name}")
    if len(chunk) > 65535:
        raise ValueError("Chunk size is too large for protocol")

    crc32 = zlib.crc32(chunk) & 0xFFFFFFFF
    if payload_mode == "binary":
        return HEADER_STRUCT.pack(
            MAGIC,
            PROTOCOL_VERSION,
            item.file_type,
            index,
            total,
            crc32,
            len(chunk),
            len(name_bytes),
        ) + name_bytes + chunk

    return b"|".join(
        [
            ASCII_MAGIC,
            str(PROTOCOL_VERSION).encode("ascii"),
            str(item.file_type).encode("ascii"),
            str(index).encode("ascii"),
            str(total).encode("ascii"),
            f"{crc32:08X}".encode("ascii"),
            str(len(chunk)).encode("ascii"),
            base64.b64encode(name_bytes),
            base64.b64encode(chunk),
        ]
    )


def build_fec_payload(
    item: TransferFile,
    total: int,
    group_index: int,
    group_start: int,
    data_count: int,
    parity_count: int,
    shard_index: int,
    shard: bytes,
) -> bytes:
    name_bytes = item.transfer_name.encode("utf-8")
    if len(name_bytes) > 65535:
        raise ValueError(f"File name is too long for protocol: {item.transfer_name}")
    if len(shard) > 65535:
        raise ValueError("FEC shard size is too large for protocol")
    if group_index > 65535:
        raise ValueError("FEC group index is too large for protocol")
    if data_count + parity_count > MAX_FEC_SHARDS:
        raise ValueError(f"FEC data + parity shards must be <= {MAX_FEC_SHARDS}")

    crc32 = zlib.crc32(shard) & 0xFFFFFFFF
    return FEC_HEADER_STRUCT.pack(
        FEC_MAGIC,
        PROTOCOL_VERSION,
        item.file_type,
        total,
        group_index,
        group_start,
        data_count,
        parity_count,
        shard_index,
        len(shard),
        crc32,
        len(name_bytes),
    ) + name_bytes + shard


def qr_error_correction_constant(qrcode_module, level: str):
    return {
        "L": qrcode_module.constants.ERROR_CORRECT_L,
        "M": qrcode_module.constants.ERROR_CORRECT_M,
        "Q": qrcode_module.constants.ERROR_CORRECT_Q,
        "H": qrcode_module.constants.ERROR_CORRECT_H,
    }[level]


def make_qr_tile(
    payload: bytes,
    args: argparse.Namespace,
    label: str | None = None,
    meta_payload: bytes | None = None,
):
    try:
        import cv2
        import numpy as np
        import qrcode
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency. Install with: pip install -r requirements-linux-encoder.txt"
        ) from exc

    qr = qrcode.QRCode(
        version=args.qr_version,
        error_correction=qr_error_correction_constant(qrcode, args.qr_error_correction),
        box_size=args.box_size,
        border=args.qr_border,
    )
    try:
        qr.add_data(payload, optimize=0)
        qr.make(fit=False)
    except Exception as exc:
        raise RuntimeError(
            "QR payload does not fit. Lower --chunk-size or raise --qr-version."
        ) from exc

    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    base_img = cv2.cvtColor(np.array(qr_img), cv2.COLOR_RGB2BGR)

    border = args.color_border
    height, width, _ = base_img.shape
    framed_img = np.full((height + border * 2, width + border * 2, 3), (0, 0, 255), dtype=np.uint8)
    framed_img[border : border + height, border : border + width] = base_img

    if args.outer_white > 0:
        pad = args.outer_white
        framed_img = cv2.copyMakeBorder(
            framed_img,
            pad,
            pad,
            pad,
            pad,
            cv2.BORDER_CONSTANT,
            value=(255, 255, 255),
        )

    if args.label_height > 0:
        height, width, _ = framed_img.shape
        strip = np.full((args.label_height, width, 3), 255, dtype=np.uint8)
        if meta_payload and args.meta_qr_size > 0:
            meta_qr = qrcode.QRCode(
                version=args.meta_qr_version,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=args.meta_qr_box_size,
                border=2,
            )
            meta_qr.add_data(meta_payload.decode("ascii"), optimize=0)
            meta_qr.make(fit=False)
            meta_img = meta_qr.make_image(fill_color="black", back_color="white").convert("RGB")
            meta_cv = cv2.cvtColor(np.array(meta_img), cv2.COLOR_RGB2BGR)
            meta_cv = cv2.resize(meta_cv, (args.meta_qr_size, args.meta_qr_size), interpolation=cv2.INTER_NEAREST)
            y0 = max(0, (args.label_height - args.meta_qr_size) // 2)
            x0 = 12
            strip[y0 : y0 + args.meta_qr_size, x0 : x0 + args.meta_qr_size] = meta_cv
        if label:
            font = cv2.FONT_HERSHEY_SIMPLEX
            scale = args.label_scale
            thickness = args.label_thickness
            (text_width, text_height), baseline = cv2.getTextSize(label, font, scale, thickness)
            x = max(8, width - text_width - 16)
            y = max(text_height + 8, (args.label_height + text_height) // 2 - baseline)
            cv2.putText(strip, label, (x, y), font, scale, (0, 0, 0), thickness, cv2.LINE_AA)
        framed_img = np.vstack([strip, framed_img])

    return framed_img


def make_qr_frame(
    payload: bytes,
    args: argparse.Namespace,
    label: str | None = None,
    meta_payload: bytes | None = None,
):
    return make_qr_tile(payload, args, label, meta_payload)


def make_grid_frame(
    packets: list[FramePacket],
    args: argparse.Namespace,
    executor: Executor | None = None,
):
    import cv2
    import numpy as np

    tile_width, tile_height = tile_size(args)
    content_width, content_height = grid_content_size(args)
    frame_width, frame_height = frame_size(args)
    capacity = args.grid_cols * args.grid_rows
    if len(packets) > capacity:
        raise ValueError("Too many packets for one grid frame")
    if content_width > frame_width or content_height > frame_height:
        raise RuntimeError(
            f"QR grid {content_width}x{content_height} does not fit canvas {frame_width}x{frame_height}. "
            "Lower --box-size, --grid-cols/rows, or raise --canvas-width/height."
        )

    frame = np.full((frame_height, frame_width, 3), 255, dtype=np.uint8)
    origin_x = max(args.grid_margin, (frame_width - content_width) // 2)
    origin_y = max(args.grid_margin, (frame_height - content_height) // 2)
    if executor is not None and len(packets) > 1:
        tiles = list(
            executor.map(
                lambda packet: make_qr_tile(packet.payload, args, packet.label, packet.meta_payload),
                packets,
            )
        )
    else:
        tiles = [make_qr_tile(packet.payload, args, packet.label, packet.meta_payload) for packet in packets]

    for slot, (packet, tile) in enumerate(zip(packets, tiles)):
        row = slot // args.grid_cols
        col = slot % args.grid_cols
        if tile.shape[0] != tile_height or tile.shape[1] != tile_width:
            raise RuntimeError("Generated tile size is not stable")
        x = origin_x + col * (tile_width + args.grid_gap)
        y = origin_y + row * (tile_height + args.grid_gap)
        frame[y : y + tile_height, x : x + tile_width] = tile

    return frame


def tile_side(args: argparse.Namespace) -> int:
    modules = args.qr_version * 4 + 17
    qr_side = (modules + args.qr_border * 2) * args.box_size
    return qr_side + args.color_border * 2 + args.outer_white * 2


def tile_size(args: argparse.Namespace) -> tuple[int, int]:
    side = tile_side(args)
    return side, side + args.label_height


def grid_content_size(args: argparse.Namespace) -> tuple[int, int]:
    tile_width, tile_height = tile_size(args)
    width = args.grid_cols * tile_width + max(0, args.grid_cols - 1) * args.grid_gap
    height = args.grid_rows * tile_height + max(0, args.grid_rows - 1) * args.grid_gap
    return width, height


def frame_size(args: argparse.Namespace) -> tuple[int, int]:
    content_width, content_height = grid_content_size(args)
    width = args.grid_margin * 2 + content_width
    height = args.grid_margin * 2 + content_height
    if args.canvas_width:
        width = args.canvas_width
    if args.canvas_height:
        height = args.canvas_height
    return width, height


def writer_fourcc(output_path: str, codec: str | None):
    import cv2

    if codec:
        return cv2.VideoWriter_fourcc(*codec)
    if output_path.lower().endswith(".avi"):
        return cv2.VideoWriter_fourcc(*"MJPG")
    return cv2.VideoWriter_fourcc(*"mp4v")


def ffmpeg_exe_suffix() -> str:
    return ".exe" if os.name == "nt" else ""


def resolve_ffmpeg(args: argparse.Namespace) -> str | None:
    candidates: list[str] = []
    if args.ffmpeg:
        candidates.append(args.ffmpeg)
    env_ffmpeg = os.environ.get("FFMPEG")
    if env_ffmpeg:
        candidates.append(env_ffmpeg)

    suffix = ffmpeg_exe_suffix()
    script_path = Path(__file__).resolve()
    bundled_roots = [
        script_path.parent,
        script_path.parent.parent,
        Path(sys.executable).resolve().parent,
        Path(sys.executable).resolve().parent.parent,
    ]
    for root in bundled_roots:
        candidates.append(str(root / f"ffmpeg{suffix}"))
        candidates.append(str(root / "bin" / f"ffmpeg{suffix}"))

    path_ffmpeg = shutil.which("ffmpeg")
    if path_ffmpeg:
        candidates.append(path_ffmpeg)

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate

    try:
        import imageio_ffmpeg

        imageio_candidate = imageio_ffmpeg.get_ffmpeg_exe()
        if imageio_candidate and Path(imageio_candidate).is_file():
            return imageio_candidate
    except Exception:
        return None
    return None


def should_transcode_for_uos(args: argparse.Namespace, output: Path) -> bool:
    return args.mp4_profile == "uos" and output.suffix.lower() == ".mp4"


def opencv_stage_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}.opencv-source{output.suffix}")


def h264_stage_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}.h264{output.suffix}")


def transcode_mp4_for_uos(source: Path, output: Path, args: argparse.Namespace) -> bool:
    ffmpeg = resolve_ffmpeg(args)
    if not ffmpeg:
        print(
            "[WARN] ffmpeg not found; keeping OpenCV mp4v output. "
            "UOS default player may not be able to play this MP4.",
            file=sys.stderr,
        )
        os.replace(source, output)
        return False

    tmp_output = h264_stage_path(output)
    if tmp_output.exists():
        tmp_output.unlink()
    command = [
        ffmpeg,
        "-y",
        "-loglevel",
        "warning",
        "-i",
        str(source),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        args.h264_preset,
        "-crf",
        str(args.h264_crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(tmp_output),
    ]
    print(f"[INFO] Converting MP4 for UOS default player with ffmpeg: {Path(ffmpeg).name}")
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0 or not tmp_output.exists():
        print(
            "[WARN] ffmpeg H.264 conversion failed; keeping OpenCV mp4v output. "
            "UOS default player may not be able to play this MP4.",
            file=sys.stderr,
        )
        os.replace(source, output)
        if tmp_output.exists():
            tmp_output.unlink()
        return False
    os.replace(tmp_output, output)
    source.unlink(missing_ok=True)
    print(f"[DONE] UOS-compatible H.264 MP4 written: {output.resolve()}")
    return True


def generate_video(items: list[TransferFile], args: argparse.Namespace) -> None:
    import cv2
    import numpy as np

    manifests = build_manifest(items, args.chunk_size)
    resume_filter = load_resume_filter(args.resume_missing)
    write_manifest_md(manifests, args, resume_filter)
    write_manifest_json(manifests, args, resume_filter)

    output_path = str(Path(args.output))
    output = Path(output_path)
    transcode_for_uos = should_transcode_for_uos(args, output)
    writer_output = opencv_stage_path(output) if transcode_for_uos else output
    width, height = frame_size(args)
    fourcc = writer_fourcc(str(writer_output), args.codec)
    white_frame = np.full((height, width, 3), 255, dtype=np.uint8)
    black_frame = np.zeros((height, width, 3), dtype=np.uint8)
    param_intro_frames = int(args.fps * args.param_intro_seconds)
    intro_frames = int(args.fps * args.intro_seconds)
    outro_frames = int(args.fps * args.outro_seconds)
    outro_frame = black_frame if args.outro_color == "black" else white_frame

    logical_total = sum(len(selected_chunk_indexes(manifest, resume_filter, args.preview_chunks)) for manifest in manifests)
    fec_total = sum(count_selected_fec_packets(manifest, resume_filter, args.preview_chunks, args) for manifest in manifests)
    physical_total = (logical_total + fec_total) * args.repeat * args.passes
    progress_total = (logical_total + fec_total) * args.passes
    progress_done = 0
    progress_last = 0
    print(f"[INFO] Files: {len(items)}")
    print(f"[INFO] Logical chunks: {logical_total}")
    if fec_enabled(args):
        print(f"[INFO] FEC parity chunks: {fec_total} ({args.fec_parity_chunks} per {args.fec_group_size} data chunks)")
    print(f"[INFO] Payload mode: {args.payload_mode}, QR ECC={args.qr_error_correction}")
    print(f"[INFO] Grid: {args.grid_cols}x{args.grid_rows}")
    content_width, content_height = grid_content_size(args)
    print(f"[INFO] QR grid content: {content_width}x{content_height}")
    print(f"[INFO] Physical QR repeats: {physical_total}")
    print(f"[INFO] Video size: {width}x{height}, fps={args.fps}, output={output_path}")
    print(f"[INFO] Workers: {args.workers}, memory budget={args.memory_gb:g}GB")
    print(f"[INFO] Parameter QR lead-in: {args.param_intro_seconds:g}s")

    packets_per_frame = args.grid_cols * args.grid_rows
    written = 0
    video_frames_written = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    if transcode_for_uos and writer_output.exists():
        writer_output.unlink()
    writer = cv2.VideoWriter(str(writer_output), fourcc, args.fps, (width, height))
    if not writer.isOpened():
        raise SystemExit(f"Could not open video writer for: {writer_output}")
    executor = ThreadPoolExecutor(max_workers=args.workers) if args.workers > 1 else None

    def emit_progress(done: int, total: int, detail: str = "") -> None:
        if total <= 0:
            return
        clamped_done = max(0, min(done, total))
        percent = clamped_done * 100 / total
        suffix = f" {detail}" if detail else ""
        print(f"[PROGRESS] encode {clamped_done}/{total} chunks {percent:.1f}%{suffix}", flush=True)

    def maybe_emit_progress(detail: str = "") -> None:
        nonlocal progress_last
        if progress_total <= 0:
            return
        if progress_done == progress_total or progress_done - progress_last >= args.progress_every:
            emit_progress(progress_done, progress_total, detail)
            progress_last = progress_done

    def write_data_frame(packets: list[FramePacket]) -> None:
        nonlocal written, video_frames_written
        frame = make_grid_frame(packets, args, executor=executor)
        if frame.shape[0] != height or frame.shape[1] != width:
            raise RuntimeError("Generated frame size is not stable")
        for _ in range(args.repeat):
            writer.write(frame)
            video_frames_written += 1
        written += len(packets) * args.repeat

    def flush_packets(pending_packets: list[FramePacket], force: bool = False) -> list[FramePacket]:
        if not force and len(pending_packets) < packets_per_frame:
            return pending_packets
        if pending_packets:
            write_data_frame(pending_packets)
        return []

    try:
        if param_intro_frames > 0:
            parameter_frame = render_parameter_intro_frame(manifests, args)
            if parameter_frame.shape[0] != height or parameter_frame.shape[1] != width:
                raise RuntimeError("Generated parameter frame size is not stable")
            for _ in range(param_intro_frames):
                writer.write(parameter_frame)
        for _ in range(intro_frames):
            writer.write(white_frame)

        emit_progress(0, progress_total, "ready")

        for pass_no in range(args.passes):
            print(f"[INFO] Writing pass {pass_no + 1}/{args.passes}")
            pending_packets: list[FramePacket] = []
            for manifest in manifests:
                item = manifest.item
                selected_indexes = selected_chunk_indexes(manifest, resume_filter, args.preview_chunks)
                selected_fec_groups = selected_fec_group_indexes(manifest, resume_filter, args.preview_chunks, args)
                if not selected_indexes and not selected_fec_groups:
                    print(f"[INFO] Skipping {item.transfer_name}; no data or FEC chunks requested.")
                    continue
                total = manifest.total_chunks
                print(
                    f"[INFO] Encoding {item.transfer_name} "
                    f"({item.path.stat().st_size} bytes, {len(selected_indexes)}/{total} chunks, "
                    f"{len(selected_fec_groups) * args.fec_parity_chunks} FEC chunks)"
                )
                group_chunks: list[bytes] = []
                group_start = 0
                for index, chunk in enumerate(iter_chunks(item.path, args.chunk_size)):
                    if index not in selected_indexes:
                        pass
                    else:
                        payload = build_frame_payload(item, index, total, chunk, args.payload_mode)
                        meta_payload = build_meta_payload(item, index, total, chunk)
                        label = f"{index + 1:06d}/{total:06d}"
                        pending_packets.append(FramePacket(payload=payload, label=label, meta_payload=meta_payload))
                        pending_packets = flush_packets(pending_packets)
                        progress_done += 1
                        maybe_emit_progress(f"{item.transfer_name} {index + 1}/{total}")
                    if fec_enabled(args):
                        group_chunks.append(chunk)
                        is_group_end = len(group_chunks) >= args.fec_group_size or index + 1 == total
                        if is_group_end:
                            group_index = group_start // args.fec_group_size
                            if group_index in selected_fec_groups:
                                shard_size = max(len(group_chunk) for group_chunk in group_chunks)
                                parities = fec_parity_shards(group_chunks, shard_size, args.fec_parity_chunks)
                                data_count = len(group_chunks)
                                for parity_offset, parity_chunk in enumerate(parities):
                                    shard_index = data_count + parity_offset
                                    payload = build_fec_payload(
                                        item,
                                        total,
                                        group_index,
                                        group_start,
                                        data_count,
                                        args.fec_parity_chunks,
                                        shard_index,
                                        parity_chunk,
                                    )
                                    label = f"FEC {group_start + 1:06d}+{parity_offset + 1:02d}"
                                    pending_packets.append(FramePacket(payload=payload, label=label, meta_payload=None))
                                    pending_packets = flush_packets(pending_packets)
                                    progress_done += 1
                                    maybe_emit_progress(f"{item.transfer_name} FEC {group_index + 1}")
                            group_start = index + 1
                            group_chunks = []
                    if (index + 1) % args.progress_every == 0 or index + 1 == total:
                        print(f"  - {item.transfer_name}: {index + 1}/{total}")
            pending_packets = flush_packets(pending_packets, force=True)
        for _ in range(outro_frames):
            writer.write(outro_frame)
    finally:
        writer.release()
        if executor is not None:
            executor.shutdown(wait=True)
    if transcode_for_uos:
        transcode_mp4_for_uos(writer_output, output, args)
    emit_progress(progress_total, progress_total, "done")
    print(f"[DONE] Video generated: {Path(output_path).resolve()}")
    print(f"[DONE] Data QR repeats written: {written}")
    print(f"[DONE] Data video frames written: {video_frames_written}")


def provided_long_options(argv: list[str]) -> set[str]:
    return {arg.split("=", 1)[0] for arg in argv if arg.startswith("--")}


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    argv_list = list(argv)
    provided_options = provided_long_options(argv_list)
    parser = argparse.ArgumentParser(
        description="Generate a red-framed QR video for optical file transfer."
    )
    parser.add_argument("inputs", nargs="*", help="Extra compressed archives. Directories and standalone files are rejected.")
    parser.add_argument("--source", action="append", help="Source archive, for example source.zip. Can be repeated.")
    parser.add_argument("-o", "--output", default="hd_secure_stream.mp4", help="Output video path.")
    parser.add_argument("--chunk-size", type=int, default=180, help="Payload bytes per QR frame.")
    parser.add_argument("--target-kbps", type=float, help="Target payload throughput in KB/s. Overrides --chunk-size.")
    parser.add_argument("--payload-mode", choices=("ascii", "binary"), default="ascii", help="QR payload protocol. binary is denser; ascii is easier to inspect.")
    parser.add_argument("--qr-version", type=int, default=20, help="Fixed QR version. Higher means more capacity.")
    parser.add_argument("--qr-error-correction", choices=("L", "M", "Q", "H"), default="H", help="QR error correction level. Lower is denser but less robust.")
    parser.add_argument("--fast-fec-4qr", action="store_true", help="Preset: 1080p horizontal 4QR, 30 FPS, repeat=2, binary payload, H ECC, FEC 100+12.")
    parser.add_argument("--box-size", type=int, default=8, help="Pixels per QR module.")
    parser.add_argument("--qr-border", type=int, default=4, help="White quiet-zone modules around QR.")
    parser.add_argument("--color-border", type=int, default=44, help="Red locator border thickness in pixels.")
    parser.add_argument("--outer-white", type=int, default=24, help="White padding outside the red locator border.")
    parser.add_argument("--label-height", type=int, default=128, help="Top label strip height in pixels. Set 0 to disable.")
    parser.add_argument("--label-scale", type=float, default=1.0, help="OpenCV font scale for the top-right chunk label.")
    parser.add_argument("--label-thickness", type=int, default=2, help="OpenCV font thickness for the top-right chunk label.")
    parser.add_argument("--meta-qr-size", type=int, default=112, help="Top-left metadata QR size in pixels. Set 0 to disable.")
    parser.add_argument("--meta-qr-version", type=int, default=4, help="QR version for the top-left metadata QR.")
    parser.add_argument("--meta-qr-box-size", type=int, default=4, help="Box size used before resizing metadata QR.")
    parser.add_argument("--fps", type=float, default=6.0, help="Video FPS.")
    parser.add_argument("--repeat", type=int, default=3, help="Repeat each QR frame this many times per pass.")
    parser.add_argument("--passes", type=int, default=2, help="Repeat the whole data stream this many times.")
    parser.add_argument("--grid-cols", type=int, default=1, help="QR tiles per video frame horizontally.")
    parser.add_argument("--grid-rows", type=int, default=1, help="QR tiles per video frame vertically.")
    parser.add_argument("--grid-gap", type=int, default=16, help="White gap between QR tiles in pixels.")
    parser.add_argument("--grid-margin", type=int, default=0, help="White margin around the QR tile grid in pixels.")
    parser.add_argument("--fec-group-size", type=int, default=100, help="Data chunks per FEC group. Used only when --fec-parity-chunks > 0.")
    parser.add_argument("--fec-parity-chunks", type=int, default=0, help="FEC parity chunks per group. 0 disables file-level FEC.")
    parser.add_argument("--canvas-width", type=int, help="Fixed output video width. QR grid is centered on a white canvas.")
    parser.add_argument("--canvas-height", type=int, help="Fixed output video height. QR grid is centered on a white canvas.")
    parser.add_argument("--param-intro-seconds", type=float, default=10.0, help="Self-describing parameter QR lead-in seconds. Set 0 to disable.")
    parser.add_argument("--intro-seconds", type=float, default=0.0, help="Optional pure white lead-in seconds after the parameter QR.")
    parser.add_argument("--outro-seconds", type=float, default=3.0, help="Tail seconds.")
    parser.add_argument("--outro-color", choices=("black", "white"), default="black", help="Tail frame color.")
    parser.add_argument("--codec", help="OpenCV FourCC codec for the intermediate writer, for example mp4v, MJPG, XVID.")
    parser.add_argument(
        "--mp4-profile",
        choices=("uos", "opencv"),
        default="uos",
        help="MP4 compatibility profile. uos transcodes final MP4 to H.264/yuv420p when ffmpeg is available.",
    )
    parser.add_argument("--ffmpeg", help="ffmpeg executable path for --mp4-profile uos. Defaults to bundled ffmpeg, FFMPEG env, or PATH.")
    parser.add_argument("--h264-crf", type=int, default=12, help="H.264 CRF for UOS-compatible MP4. Lower is clearer/larger.")
    parser.add_argument("--h264-preset", default="veryfast", help="ffmpeg libx264 preset for UOS-compatible MP4.")
    parser.add_argument("--workers", type=int, default=0, help="QR rendering workers. 0 auto-selects up to 6 workers.")
    parser.add_argument("--memory-gb", type=float, default=6.0, help="Memory budget advertised to the decoder parameter QR.")
    parser.add_argument("--progress-every", type=int, default=100, help="Progress print interval in chunks.")
    parser.add_argument("--manifest", help="Write chunk checksum Markdown to this path. Defaults to <output>.manifest.md.")
    parser.add_argument("--manifest-json", help="Write machine-readable manifest JSON. Defaults to <output>.manifest.json.")
    parser.add_argument("--resume-missing", help="missing_chunks.json from the decoder; encode only those chunks.")
    parser.add_argument("--preview-chunks", type=int, help="Encode only the first N selected chunks per file for quick phone-recording tests.")
    args = parser.parse_args(argv_list)

    if args.fast_fec_4qr:
        def preset(flag: str, attr: str, value) -> None:
            if flag not in provided_options:
                setattr(args, attr, value)

        preset("--payload-mode", "payload_mode", "binary")
        preset("--qr-error-correction", "qr_error_correction", "H")
        preset("--qr-version", "qr_version", 40)
        preset("--box-size", "box_size", 2)
        preset("--chunk-size", "chunk_size", 1200)
        preset("--fps", "fps", 30.0)
        preset("--repeat", "repeat", 2)
        preset("--passes", "passes", 1)
        preset("--grid-cols", "grid_cols", 4)
        preset("--grid-rows", "grid_rows", 1)
        preset("--grid-gap", "grid_gap", 10)
        preset("--canvas-width", "canvas_width", 1920)
        preset("--canvas-height", "canvas_height", 1080)
        preset("--label-height", "label_height", 64)
        preset("--label-scale", "label_scale", 0.45)
        preset("--label-thickness", "label_thickness", 1)
        preset("--meta-qr-size", "meta_qr_size", 48)
        preset("--meta-qr-version", "meta_qr_version", 6)
        preset("--meta-qr-box-size", "meta_qr_box_size", 2)
        preset("--color-border", "color_border", 8)
        preset("--outer-white", "outer_white", 4)
        preset("--fec-group-size", "fec_group_size", 100)
        preset("--fec-parity-chunks", "fec_parity_chunks", 12)
        preset("--param-intro-seconds", "param_intro_seconds", 10.0)

    if args.chunk_size < 1 or args.chunk_size > 65535:
        parser.error("--chunk-size must be between 1 and 65535")
    if args.repeat < 1 or args.passes < 1:
        parser.error("--repeat and --passes must be positive")
    if args.grid_cols < 1 or args.grid_rows < 1:
        parser.error("--grid-cols and --grid-rows must be positive")
    if args.grid_gap < 0 or args.grid_margin < 0:
        parser.error("--grid-gap and --grid-margin must not be negative")
    if args.fec_group_size < 1:
        parser.error("--fec-group-size must be positive")
    if args.fec_parity_chunks < 0:
        parser.error("--fec-parity-chunks must not be negative")
    if args.fec_parity_chunks > 0 and args.fec_group_size + args.fec_parity_chunks > MAX_FEC_SHARDS:
        parser.error(f"--fec-group-size + --fec-parity-chunks must be <= {MAX_FEC_SHARDS}")
    if (args.canvas_width is not None and args.canvas_width < 1) or (args.canvas_height is not None and args.canvas_height < 1):
        parser.error("--canvas-width and --canvas-height must be positive")
    if args.param_intro_seconds < 0 or args.intro_seconds < 0 or args.outro_seconds < 0:
        parser.error("--param-intro-seconds, --intro-seconds, and --outro-seconds must not be negative")
    if args.h264_crf < 0 or args.h264_crf > 51:
        parser.error("--h264-crf must be between 0 and 51")
    if args.workers < 0:
        parser.error("--workers must be zero or positive")
    if args.workers == 0:
        args.workers = max(1, min(6, os.cpu_count() or 1))
    if args.memory_gb <= 0:
        parser.error("--memory-gb must be positive")
    if args.qr_version < 1 or args.qr_version > 40:
        parser.error("--qr-version must be between 1 and 40")
    if args.preview_chunks is not None and args.preview_chunks < 1:
        parser.error("--preview-chunks must be positive")
    if args.target_kbps is not None:
        if args.target_kbps <= 0:
            parser.error("--target-kbps must be positive")
        args.chunk_size = max(1, min(65535, int(args.target_kbps * 1024 * args.repeat / args.fps)))
        print(f"[INFO] --target-kbps set chunk size to {args.chunk_size} bytes")
    if args.meta_qr_size > 0 and args.label_height < args.meta_qr_size + 8:
        args.label_height = args.meta_qr_size + 8
    content_width, content_height = grid_content_size(args)
    frame_width, frame_height = frame_size(args)
    if content_width + args.grid_margin * 2 > frame_width or content_height + args.grid_margin * 2 > frame_height:
        parser.error(
            f"QR grid {content_width}x{content_height} plus margins does not fit canvas {frame_width}x{frame_height}"
        )
    return args


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    items = collect_inputs(args)
    if not items:
        print("No input files found.", file=sys.stderr)
        print("Example: python3 scripts/linux_encoder_hd.py --source source.zip", file=sys.stderr)
        return 2

    generate_video(items, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
