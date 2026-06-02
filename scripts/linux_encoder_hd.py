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
import hashlib
import json
import math
import os
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator


MAGIC = b"QVT1"
ASCII_MAGIC = b"QVT2"
PROTOCOL_VERSION = 1
FILE_TYPE_SOURCE = 0x01
FILE_TYPE_JAR = 0x02
HEADER_STRUCT = struct.Struct(">4sBBIIIHH")
HEADER_SIZE = HEADER_STRUCT.size


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
    meta_payload: bytes


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


def collect_lib_jars(lib_dir: Path) -> list[TransferFile]:
    files: list[TransferFile] = []
    for jar_path in sorted(lib_dir.rglob("*.jar")):
        if jar_path.is_file():
            rel_name = normalize_transfer_name(jar_path.relative_to(lib_dir).as_posix())
            files.append(TransferFile(jar_path, FILE_TYPE_JAR, rel_name))
    return files


def collect_inputs(args: argparse.Namespace) -> list[TransferFile]:
    items: list[TransferFile] = []

    for source in args.source or []:
        path = Path(source)
        if path.is_file():
            items.append(TransferFile(path, FILE_TYPE_SOURCE, normalize_transfer_name(path.name)))
        else:
            print(f"[WARN] Source archive not found, skipped: {path}", file=sys.stderr)

    for lib_dir in args.lib_dir or []:
        path = Path(lib_dir)
        if path.is_dir():
            jars = collect_lib_jars(path)
            items.extend(jars)
            if not jars:
                print(f"[WARN] No .jar files found under: {path}", file=sys.stderr)
        else:
            print(f"[WARN] Lib directory not found, skipped: {path}", file=sys.stderr)

    for raw in args.inputs:
        path = Path(raw)
        if path.is_dir():
            jars = collect_lib_jars(path)
            items.extend(jars)
            if not jars:
                print(f"[WARN] Directory has no .jar files, skipped: {path}", file=sys.stderr)
            continue

        if not path.is_file():
            print(f"[WARN] Input path not found, skipped: {path}", file=sys.stderr)
            continue

        file_type = FILE_TYPE_JAR if path.suffix.lower() == ".jar" else FILE_TYPE_SOURCE
        items.append(TransferFile(path, file_type, normalize_transfer_name(path.name)))

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
        type_label = "jar" if manifest.item.file_type == FILE_TYPE_JAR else "source"
        encoded = len(selected_chunk_indexes(manifest, resume_filter, args.preview_chunks))
        lines.append(
            f"| {type_label} | `{manifest.item.transfer_name}` | {manifest.size} | "
            f"`{manifest.sha256}` | {manifest.total_chunks} | {encoded} |"
        )

    for manifest in manifests:
        type_label = "jar" if manifest.item.file_type == FILE_TYPE_JAR else "source"
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
        "resume_filter": args.resume_missing,
        "files": [],
    }
    for manifest in manifests:
        selected = selected_chunk_indexes(manifest, resume_filter, args.preview_chunks)
        data["files"].append(
            {
                "file_type": manifest.item.file_type,
                "file_name": manifest.item.transfer_name,
                "source_path": str(manifest.item.path),
                "size": manifest.size,
                "sha256": manifest.sha256,
                "total": manifest.total_chunks,
                "encoded_chunks": sorted(selected),
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


def make_grid_frame(packets: list[FramePacket], args: argparse.Namespace):
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
    for slot, packet in enumerate(packets):
        row = slot // args.grid_cols
        col = slot % args.grid_cols
        tile = make_qr_tile(packet.payload, args, packet.label, packet.meta_payload)
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


def generate_video(items: list[TransferFile], args: argparse.Namespace) -> None:
    import cv2
    import numpy as np

    manifests = build_manifest(items, args.chunk_size)
    resume_filter = load_resume_filter(args.resume_missing)
    write_manifest_md(manifests, args, resume_filter)
    write_manifest_json(manifests, args, resume_filter)

    output_path = str(Path(args.output))
    width, height = frame_size(args)
    fourcc = writer_fourcc(output_path, args.codec)
    writer = cv2.VideoWriter(output_path, fourcc, args.fps, (width, height))
    if not writer.isOpened():
        raise SystemExit(f"Could not open video writer for: {output_path}")

    white_frame = np.full((height, width, 3), 255, dtype=np.uint8)
    black_frame = np.zeros((height, width, 3), dtype=np.uint8)
    intro_frames = int(args.fps * args.intro_seconds)
    outro_frames = int(args.fps * args.outro_seconds)
    outro_frame = black_frame if args.outro_color == "black" else white_frame

    logical_total = sum(len(selected_chunk_indexes(manifest, resume_filter, args.preview_chunks)) for manifest in manifests)
    physical_total = logical_total * args.repeat * args.passes
    print(f"[INFO] Files: {len(items)}")
    print(f"[INFO] Logical chunks: {logical_total}")
    print(f"[INFO] Payload mode: {args.payload_mode}, QR ECC={args.qr_error_correction}")
    print(f"[INFO] Grid: {args.grid_cols}x{args.grid_rows}")
    content_width, content_height = grid_content_size(args)
    print(f"[INFO] QR grid content: {content_width}x{content_height}")
    print(f"[INFO] Physical QR repeats: {physical_total}")
    print(f"[INFO] Video size: {width}x{height}, fps={args.fps}, output={output_path}")

    for _ in range(intro_frames):
        writer.write(white_frame)

    written = 0
    video_frames_written = 0
    try:
        for pass_no in range(args.passes):
            print(f"[INFO] Writing pass {pass_no + 1}/{args.passes}")
            pending_packets: list[FramePacket] = []
            for manifest in manifests:
                item = manifest.item
                selected_indexes = selected_chunk_indexes(manifest, resume_filter, args.preview_chunks)
                if not selected_indexes:
                    print(f"[INFO] Skipping {item.transfer_name}; no missing chunks requested.")
                    continue
                total = manifest.total_chunks
                print(
                    f"[INFO] Encoding {item.transfer_name} "
                    f"({item.path.stat().st_size} bytes, {len(selected_indexes)}/{total} chunks)"
                )
                for index, chunk in enumerate(iter_chunks(item.path, args.chunk_size)):
                    if index not in selected_indexes:
                        continue
                    payload = build_frame_payload(item, index, total, chunk, args.payload_mode)
                    meta_payload = build_meta_payload(item, index, total, chunk)
                    label = f"{index + 1:06d}/{total:06d}"
                    pending_packets.append(FramePacket(payload=payload, label=label, meta_payload=meta_payload))
                    if len(pending_packets) >= args.grid_cols * args.grid_rows:
                        frame = make_grid_frame(pending_packets, args)
                        if frame.shape[0] != height or frame.shape[1] != width:
                            raise RuntimeError("Generated frame size is not stable")
                        for _ in range(args.repeat):
                            writer.write(frame)
                            video_frames_written += 1
                        written += len(pending_packets) * args.repeat
                        pending_packets = []
                    if (index + 1) % args.progress_every == 0 or index + 1 == total:
                        print(f"  - {item.transfer_name}: {index + 1}/{total}")
            if pending_packets:
                frame = make_grid_frame(pending_packets, args)
                if frame.shape[0] != height or frame.shape[1] != width:
                    raise RuntimeError("Generated frame size is not stable")
                for _ in range(args.repeat):
                    writer.write(frame)
                    video_frames_written += 1
                written += len(pending_packets) * args.repeat
    finally:
        for _ in range(outro_frames):
            writer.write(outro_frame)
        writer.release()

    print(f"[DONE] Video generated: {Path(output_path).resolve()}")
    print(f"[DONE] Data QR repeats written: {written}")
    print(f"[DONE] Data video frames written: {video_frames_written}")


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a red-framed QR video for optical file transfer."
    )
    parser.add_argument("inputs", nargs="*", help="Extra files or lib directories. Directories are scanned for .jar files.")
    parser.add_argument("--source", action="append", help="Source archive, for example source.zip. Can be repeated.")
    parser.add_argument("--lib-dir", action="append", help="Directory containing .jar files. Can be repeated.")
    parser.add_argument("-o", "--output", default="hd_secure_stream.mp4", help="Output video path.")
    parser.add_argument("--chunk-size", type=int, default=180, help="Payload bytes per QR frame.")
    parser.add_argument("--target-kbps", type=float, help="Target payload throughput in KB/s. Overrides --chunk-size.")
    parser.add_argument("--payload-mode", choices=("ascii", "binary"), default="ascii", help="QR payload protocol. binary is denser; ascii is easier to inspect.")
    parser.add_argument("--qr-version", type=int, default=20, help="Fixed QR version. Higher means more capacity.")
    parser.add_argument("--qr-error-correction", choices=("L", "M", "Q", "H"), default="H", help="QR error correction level. Lower is denser but less robust.")
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
    parser.add_argument("--canvas-width", type=int, help="Fixed output video width. QR grid is centered on a white canvas.")
    parser.add_argument("--canvas-height", type=int, help="Fixed output video height. QR grid is centered on a white canvas.")
    parser.add_argument("--intro-seconds", type=float, default=3.0, help="Pure white lead-in seconds.")
    parser.add_argument("--outro-seconds", type=float, default=3.0, help="Tail seconds.")
    parser.add_argument("--outro-color", choices=("black", "white"), default="black", help="Tail frame color.")
    parser.add_argument("--codec", help="FourCC codec, for example mp4v, MJPG, XVID.")
    parser.add_argument("--progress-every", type=int, default=100, help="Progress print interval in chunks.")
    parser.add_argument("--manifest", help="Write chunk checksum Markdown to this path. Defaults to <output>.manifest.md.")
    parser.add_argument("--manifest-json", help="Write machine-readable manifest JSON. Defaults to <output>.manifest.json.")
    parser.add_argument("--resume-missing", help="missing_chunks.json from the decoder; encode only those chunks.")
    parser.add_argument("--preview-chunks", type=int, help="Encode only the first N selected chunks per file for quick phone-recording tests.")
    args = parser.parse_args(list(argv))

    if args.chunk_size < 1 or args.chunk_size > 65535:
        parser.error("--chunk-size must be between 1 and 65535")
    if args.repeat < 1 or args.passes < 1:
        parser.error("--repeat and --passes must be positive")
    if args.grid_cols < 1 or args.grid_rows < 1:
        parser.error("--grid-cols and --grid-rows must be positive")
    if args.grid_gap < 0 or args.grid_margin < 0:
        parser.error("--grid-gap and --grid-margin must not be negative")
    if (args.canvas_width is not None and args.canvas_width < 1) or (args.canvas_height is not None and args.canvas_height < 1):
        parser.error("--canvas-width and --canvas-height must be positive")
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
        print("Example: python3 scripts/linux_encoder_hd.py --source source.zip --lib-dir lib", file=sys.stderr)
        return 2

    generate_video(items, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
