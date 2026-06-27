#!/usr/bin/env python3
"""
Targeted supplemental scanner for noisy 2-QR screen recordings.

The normal decoder scans every frame with the fast full-frame path. This helper
is intentionally narrower: it reads an existing decode state, estimates where
the remaining missing chunk ranges should appear in the video, and tries harder
around those windows with cropped QR regions, image variants, and temporal
majority voting. Any recovered chunks are merged back into the state directory.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import statistics
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import win_decoder_hd as decoder

DEEP_SCAN = False


@dataclass(frozen=True)
class ChunkHit:
    file_type: int
    file_name: str
    index0: int
    total: int
    payload: bytes
    frame_no: int
    qr_box: tuple[int, int, int, int] | None

    @property
    def index1(self) -> int:
        return self.index0 + 1


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def imwrite(path: Path, image: np.ndarray) -> bool:
    """Write images to Unicode Windows paths."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix or ".jpg"
    ok, encoded = cv2.imencode(ext, image)
    if not ok:
        return False
    path.write_bytes(encoded.tobytes())
    return True


def parse_box(position) -> tuple[int, int, int, int] | None:
    points = [(int(x), int(y)) for x, y in re.findall(r"(\d+)x(\d+)", str(position))]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def pad_box(box: tuple[int, int, int, int], width: int, height: int, margin: int = 24) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    return max(0, x1 - margin), max(0, y1 - margin), min(width, x2 + margin), min(height, y2 + margin)


def crop_box(frame: np.ndarray, box: tuple[int, int, int, int], margin: int = 24) -> np.ndarray:
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = pad_box(box, width, height, margin)
    return frame[y1:y2, x1:x2]


def state_key(file_type: int, file_name: str) -> str:
    return f"{file_type}:{decoder.safe_relative_name(file_name)}"


def state_token(file_type: int, file_name: str) -> str:
    raw = state_key(file_type, file_name).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def load_missing(state_dir: Path) -> dict[tuple[int, str], dict]:
    missing_path = state_dir / "missing_chunks.json"
    if not missing_path.exists():
        raise SystemExit(f"missing_chunks.json not found: {missing_path}")
    data = json.loads(read_text(missing_path))
    result = {}
    for entry in data.get("files", []):
        file_type = int(entry["file_type"])
        file_name = decoder.safe_relative_name(str(entry["file_name"]))
        result[(file_type, file_name)] = entry
    return result


def load_state_data(state_dir: Path) -> dict:
    state_path = state_dir / "state.json"
    if not state_path.exists():
        return {"protocol": "QVT1", "files": []}
    return json.loads(read_text(state_path))


def load_state_chunks(state_dir: Path) -> dict[tuple[int, str], set[int]]:
    data = load_state_data(state_dir)
    result: dict[tuple[int, str], set[int]] = {}
    for entry in data.get("files", []):
        file_type = int(entry["file_type"])
        file_name = decoder.safe_relative_name(str(entry["file_name"]))
        result[(file_type, file_name)] = {int(index) for index in entry.get("chunks", [])}
    return result


def save_state_chunks(state_dir: Path, chunk_sets: dict[tuple[int, str], set[int]], totals: dict[tuple[int, str], int]) -> None:
    files = []
    for key in sorted(chunk_sets, key=lambda item: (item[0], item[1])):
        file_type, file_name = key
        files.append(
            {
                "file_type": file_type,
                "file_name": file_name,
                "total": int(totals[key]),
                "chunks": sorted(chunk_sets[key]),
            }
        )
    data = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "protocol": "QVT1",
        "files": files,
    }
    write_json(state_dir / "state.json", data)


def refresh_missing_reports(state_dir: Path, chunk_sets: dict[tuple[int, str], set[int]], totals: dict[tuple[int, str], int]) -> None:
    generated_at = datetime.now().isoformat(timespec="seconds")
    report = {"generated_at": generated_at, "files": []}
    lines = [
        "# QR Video Decode Missing Report",
        "",
        f"- Generated at: {generated_at}",
        "",
        "| Type | File | Received | Total | Missing count |",
        "|---|---|---:|---:|---:|",
    ]

    for (file_type, file_name) in sorted(chunk_sets, key=lambda item: (item[0], item[1])):
        total = totals[(file_type, file_name)]
        chunks = chunk_sets[(file_type, file_name)]
        missing = [index for index in range(total) if index not in chunks]
        type_label = "jar" if file_type == decoder.FILE_TYPE_JAR else "source"
        lines.append(f"| {type_label} | `{file_name}` | {len(chunks)} | {total} | {len(missing)} |")
        report["files"].append(
            {
                "file_type": file_type,
                "file_name": file_name,
                "total": total,
                "received_chunks": sorted(chunks),
                "missing_chunks": missing,
            }
        )

    for entry in report["files"]:
        type_label = "jar" if entry["file_type"] == decoder.FILE_TYPE_JAR else "source"
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

    write_json(state_dir / "missing_chunks.json", report)
    (state_dir / "missing_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_chunk_file(state_dir: Path, hit: ChunkHit) -> bool:
    token = state_token(hit.file_type, hit.file_name)
    chunk_dir = state_dir / "chunks" / token
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunk_dir / f"{hit.index0:08d}.bin"
    if chunk_path.exists():
        return False
    chunk_path.write_bytes(hit.payload)
    return True


def parse_payload_from_result(result, frame_no: int) -> ChunkHit | None:
    raw = bytes(result.bytes) if result.bytes else result.text.encode("utf-8", errors="ignore")
    if not raw:
        return None
    try:
        file_type, index0, total, file_name, payload = decoder.parse_payload(raw)
    except Exception:
        return None
    return ChunkHit(
        file_type=file_type,
        file_name=file_name,
        index0=index0,
        total=total,
        payload=payload,
        frame_no=frame_no,
        qr_box=parse_box(getattr(result, "position", "")),
    )


def image_variants(image: np.ndarray) -> Iterable[np.ndarray]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    yield gray

    yield cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    contrast = clahe.apply(gray)
    yield contrast

    blur = cv2.GaussianBlur(contrast, (0, 0), 1.1)
    sharpened = cv2.addWeighted(contrast, 1.7, blur, -0.7, 0)
    yield sharpened

    _, otsu = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield otsu

    adaptive = cv2.adaptiveThreshold(
        contrast,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        51,
        2,
    )
    yield adaptive

    if not DEEP_SCAN:
        return

    for scale in (1.5, 3.0):
        yield cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    yield cv2.GaussianBlur(gray, (3, 3), 0)
    yield cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
    yield cv2.bitwise_not(otsu)

    kernel = np.ones((2, 2), np.uint8)
    yield cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, kernel)
    yield cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, kernel)


def zxing_hits(image: np.ndarray, frame_no: int) -> list[ChunkHit]:
    import zxingcpp

    hits: list[ChunkHit] = []
    seen = set()
    for variant in image_variants(image):
        flag_sets = [
            (False, False, False),
            (False, True, False),
            (True, False, False),
            (False, False, True),
        ]
        if DEEP_SCAN:
            flag_sets.extend(
                [
                    (True, True, False),
                    (True, False, True),
                    (False, True, True),
                    (True, True, True),
                ]
            )
        for try_downscale, try_invert, is_pure in flag_sets:
            try:
                results = zxingcpp.read_barcodes(
                    variant,
                    formats=zxingcpp.BarcodeFormat.QRCode,
                    try_rotate=True,
                    try_downscale=try_downscale,
                    try_invert=try_invert,
                    is_pure=is_pure,
                    return_errors=False,
                )
            except Exception:
                continue
            for result in results:
                hit = parse_payload_from_result(result, frame_no)
                if not hit:
                    continue
                marker = (hit.file_type, hit.file_name, hit.index0, zlib.crc32(hit.payload))
                if marker in seen:
                    continue
                seen.add(marker)
                hits.append(hit)
    return hits


def opencv_hits(image: np.ndarray, frame_no: int) -> list[ChunkHit]:
    detector = cv2.QRCodeDetector()
    hits: list[ChunkHit] = []
    seen = set()
    for variant in image_variants(image):
        candidates = []
        try:
            ok, decoded_info, _points, _ = detector.detectAndDecodeMulti(variant)
            if ok:
                candidates.extend(text.encode("utf-8", errors="ignore") for text in decoded_info if text)
        except Exception:
            pass
        try:
            text, _points, _ = detector.detectAndDecode(variant)
            if text:
                candidates.append(text.encode("utf-8", errors="ignore"))
        except Exception:
            pass
        for raw in candidates:
            try:
                file_type, index0, total, file_name, payload = decoder.parse_payload(raw)
            except Exception:
                continue
            marker = (file_type, file_name, index0, zlib.crc32(payload))
            if marker in seen:
                continue
            seen.add(marker)
            hits.append(ChunkHit(file_type, file_name, index0, total, payload, frame_no, None))
    return hits


def decode_hits(image: np.ndarray, frame_no: int) -> list[ChunkHit]:
    hits = zxing_hits(image, frame_no)
    if hits:
        return hits
    return opencv_hits(image, frame_no)


def ranges(indexes: Iterable[int]) -> list[tuple[int, int]]:
    nums = sorted(set(int(index) for index in indexes))
    if not nums:
        return []
    output = []
    start = previous = nums[0]
    for item in nums[1:]:
        if item == previous + 1:
            previous = item
        else:
            output.append((start, previous))
            start = previous = item
    output.append((start, previous))
    return output


def collect_anchors(video_path: Path, target_keys: set[tuple[int, str]], sample_step: int, max_anchors_per_key: int) -> dict[tuple[int, str], list[tuple[int, int]]]:
    import zxingcpp

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise SystemExit(f"Could not open video: {video_path}")
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    anchors: dict[tuple[int, str], list[tuple[int, int]]] = {key: [] for key in target_keys}
    decoded_frames = 0

    for frame_no in range(0, frame_count, sample_step):
        if all(len(values) >= max_anchors_per_key for values in anchors.values()):
            break
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = cap.read()
        if not ok:
            continue
        decoded_frames += 1
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        try:
            results = zxingcpp.read_barcodes(
                gray,
                formats=zxingcpp.BarcodeFormat.QRCode,
                try_rotate=False,
                try_downscale=False,
                try_invert=False,
                is_pure=False,
                return_errors=False,
            )
        except Exception:
            continue
        for result in results:
            hit = parse_payload_from_result(result, frame_no)
            if not hit:
                continue
            key = (hit.file_type, hit.file_name)
            if key in anchors and len(anchors[key]) < max_anchors_per_key:
                anchors[key].append((hit.index0, frame_no))

    cap.release()
    print(f"[INFO] Anchor sampling frames={decoded_frames}, anchors={{{', '.join(f'{k[1]}:{len(v)}' for k, v in anchors.items())}}}")
    return anchors


def fit_frame_mapping(anchors: list[tuple[int, int]]) -> tuple[float, float] | None:
    unique = sorted(set(anchors))
    if len(unique) < 2:
        return None

    slopes = []
    for i, (chunk_a, frame_a) in enumerate(unique):
        for chunk_b, frame_b in unique[i + 1 :]:
            if chunk_b == chunk_a:
                continue
            slope = (frame_b - frame_a) / (chunk_b - chunk_a)
            if 1.0 <= slope <= 30.0:
                slopes.append(slope)
    if not slopes:
        return None

    slope = statistics.median(slopes)
    intercepts = [frame - slope * chunk for chunk, frame in unique]
    intercept = statistics.median(intercepts)
    return slope, intercept


def estimate_pair_boxes(video_path: Path, anchors: list[tuple[int, int]], mapping: tuple[float, float] | None) -> list[tuple[int, int, int, int]]:
    import zxingcpp

    cap = cv2.VideoCapture(str(video_path))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    candidate_frames = set()
    for _chunk, frame_no in anchors[:20]:
        candidate_frames.add(frame_no)
    if mapping:
        slope, intercept = mapping
        for chunk, _frame in anchors[:20]:
            candidate_frames.add(max(0, min(frame_count - 1, int(round(slope * chunk + intercept)))))

    boxes = []
    for frame_no in sorted(candidate_frames):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        try:
            results = zxingcpp.read_barcodes(
                gray,
                formats=zxingcpp.BarcodeFormat.QRCode,
                try_rotate=False,
                try_downscale=False,
                try_invert=False,
                is_pure=False,
                return_errors=False,
            )
        except Exception:
            continue
        for result in results:
            box = parse_box(getattr(result, "position", ""))
            if box:
                boxes.append(box)
    cap.release()

    if not boxes:
        return []

    groups = {0: [], 1: []}
    for box in boxes:
        center_x = (box[0] + box[2]) / 2.0
        groups[0 if center_x < 900 else 1].append(box)

    output = []
    for group_boxes in groups.values():
        if not group_boxes:
            continue
        coords = list(zip(*group_boxes))
        output.append(tuple(int(statistics.median(values)) for values in coords))
    return output


def voted_image(images: list[np.ndarray], size: int = 760) -> np.ndarray:
    binaries = []
    for image in images:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        resized = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
        blurred = cv2.GaussianBlur(resized, (3, 3), 0)
        _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        binaries.append(binary)
    stack = np.stack(binaries, axis=0)
    black_votes = np.sum(stack == 0, axis=0)
    return np.where(black_votes > (len(images) / 2), 0, 255).astype("uint8")


def scan_window(
    video_path: Path,
    frame_start: int,
    frame_end: int,
    target_missing: dict[tuple[int, str], set[int]],
    boxes: list[tuple[int, int, int, int]],
    artifact_dir: Path,
    label: str,
    save_images: bool,
    full_frame_stride: int,
    frame_stride: int,
) -> dict[tuple[int, str], dict[int, ChunkHit]]:
    cap = cv2.VideoCapture(str(video_path))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_start = max(0, min(frame_count - 1, frame_start))
    frame_end = max(frame_start, min(frame_count - 1, frame_end))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_start)

    recovered: dict[tuple[int, str], dict[int, ChunkHit]] = {}
    crop_buffers: dict[tuple[int, int], list[np.ndarray]] = {}

    step = max(1, frame_stride)
    for frame_no in range(frame_start, frame_end + 1, step):
        ok, frame = cap.read()
        if not ok:
            break
        if step > 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no + step)

        images = [frame] if full_frame_stride > 0 and frame_no % full_frame_stride == 0 else []
        for box_id, box in enumerate(boxes):
            crop = crop_box(frame, box, margin=28)
            images.append(crop)
            crop_buffers.setdefault((box_id, frame_no // 8), []).append(crop)

        for image in images:
            for hit in decode_hits(image, frame_no):
                key = (hit.file_type, hit.file_name)
                missing = target_missing.get(key)
                if not missing or hit.index0 not in missing:
                    continue
                recovered.setdefault(key, {})[hit.index0] = hit
                if save_images:
                    chunk1 = hit.index1
                    subdir = artifact_dir / "recovered_chunks" / safe_fragment(key[1])
                    imwrite(subdir / f"{label}_chunk_{chunk1:05d}_frame_{frame_no:06d}_full.jpg", frame)
                    if hit.qr_box:
                        imwrite(subdir / f"{label}_chunk_{chunk1:05d}_frame_{frame_no:06d}_qr.jpg", crop_box(frame, hit.qr_box))

        if all(index in recovered.get(key, {}) for key, indexes in target_missing.items() for index in indexes):
            break

    # Try temporal voting over adjacent crop groups. This is especially useful
    # when individual repeated frames are noisy but the black/white modules are stable.
    for (box_id, group), crops in list(crop_buffers.items()):
        if len(crops) < 3:
            continue
        for window_size in (5, 7, 9):
            if len(crops) < window_size:
                continue
            for start in range(0, len(crops) - window_size + 1):
                voted = voted_image(crops[start : start + window_size])
                frame_no = group * 8 + start
                for hit in decode_hits(voted, frame_no):
                    key = (hit.file_type, hit.file_name)
                    missing = target_missing.get(key)
                    if not missing or hit.index0 not in missing:
                        continue
                    recovered.setdefault(key, {})[hit.index0] = hit
                    if save_images:
                        subdir = artifact_dir / "recovered_chunks" / safe_fragment(key[1])
                        imwrite(subdir / f"{label}_voted_chunk_{hit.index1:05d}_group_{group:06d}_box_{box_id}.png", voted)

    cap.release()
    return recovered


def safe_fragment(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_") or "file"


def save_missing_window_images(
    video_path: Path,
    windows: list[tuple[str, int, int]],
    boxes: list[tuple[int, int, int, int]],
    artifact_dir: Path,
) -> None:
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    for label, start, end in windows:
        for frame_no in sorted({start, (start + end) // 2, end}):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ok, frame = cap.read()
            if not ok:
                continue
            subdir = artifact_dir / "missing_window_images" / label
            imwrite(subdir / f"frame_{frame_no:06d}_t_{frame_no / fps:08.2f}_full.jpg", frame)
            for i, box in enumerate(boxes):
                imwrite(subdir / f"frame_{frame_no:06d}_t_{frame_no / fps:08.2f}_box_{i + 1}.jpg", crop_box(frame, box))
    cap.release()


def reconstruct_from_state(state_dir: Path, output_dir: Path) -> int:
    db = decoder.load_state(state_dir)
    decoder.write_missing_reports(db, state_dir)
    return decoder.reconstruct_files(db, output_dir)


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Targeted supplemental QR video scanner.")
    parser.add_argument("video", help="Video file to supplement.")
    parser.add_argument("--state-dir", required=True, help="Existing decoder state directory.")
    parser.add_argument("-o", "--output", required=True, help="Reconstruction output directory.")
    parser.add_argument("--artifact-dir", required=True, help="Directory for reports and diagnostic images.")
    parser.add_argument("--sample-step", type=int, default=900, help="Frame step used for mapping anchors.")
    parser.add_argument("--max-anchors", type=int, default=60, help="Maximum anchors per file.")
    parser.add_argument("--window-margin", type=int, default=900, help="Extra frames around estimated missing range.")
    parser.add_argument("--save-images", action="store_true", help="Save recovered and missing-window images.")
    parser.add_argument("--deep", action="store_true", help="Use slower exhaustive image variants and zxing flags.")
    parser.add_argument("--frame-stride", type=int, default=1, help="Scan every Nth frame in targeted windows.")
    parser.add_argument("--full-frame-stride", type=int, default=30, help="Try full-frame decode every N frames; crops are decoded every scanned frame. Use 0 to disable.")
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    global DEEP_SCAN
    args = parse_args(argv)
    DEEP_SCAN = bool(args.deep)
    video_path = Path(args.video)
    state_dir = Path(args.state_dir)
    output_dir = Path(args.output)
    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    missing_entries = load_missing(state_dir)
    state_chunks = load_state_chunks(state_dir)
    totals = {key: int(entry["total"]) for key, entry in missing_entries.items()}

    target_missing: dict[tuple[int, str], set[int]] = {}
    for key, entry in missing_entries.items():
        missing = {int(index) for index in entry.get("missing_chunks", [])}
        if missing:
            target_missing[key] = missing

    if not target_missing:
        print("[DONE] No missing chunks to supplement.")
        return reconstruct_from_state(state_dir, output_dir)

    anchors = collect_anchors(video_path, set(target_missing), args.sample_step, args.max_anchors)
    mappings = {key: fit_frame_mapping(values) for key, values in anchors.items()}
    all_boxes = []
    for key, key_anchors in anchors.items():
        boxes = estimate_pair_boxes(video_path, key_anchors, mappings.get(key))
        for box in boxes:
            if box not in all_boxes:
                all_boxes.append(box)
    if not all_boxes:
        all_boxes = [(345, 327, 832, 815), (916, 327, 1403, 815)]
    print(f"[INFO] QR boxes: {all_boxes}")

    cap = cv2.VideoCapture(str(video_path))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()

    windows = []
    for key, missing in target_missing.items():
        mapping = mappings.get(key)
        if not mapping:
            print(f"[WARN] Could not estimate frame mapping for {key[1]}; skipping targeted windows.")
            continue
        slope, intercept = mapping
        print(f"[INFO] Mapping {key[1]}: frame ~= {slope:.4f} * chunk0 + {intercept:.1f}")
        for start_index, end_index in ranges(missing):
            start_frame = int(math.floor(slope * start_index + intercept)) - args.window_margin
            end_frame = int(math.ceil(slope * end_index + intercept)) + args.window_margin
            start_frame = max(0, min(frame_count - 1, start_frame))
            end_frame = max(start_frame, min(frame_count - 1, end_frame))
            label = f"{safe_fragment(key[1])}_{start_index + 1:05d}_{end_index + 1:05d}"
            windows.append((key, label, start_frame, end_frame, set(range(start_index, end_index + 1))))

    recovered_total = 0
    recovered_report = []
    diagnostic_windows = []
    for key, label, start_frame, end_frame, indexes in windows:
        remaining = indexes & target_missing.get(key, set())
        if not remaining:
            continue
        print(f"[INFO] Scanning {key[1]} chunks {min(remaining) + 1}-{max(remaining) + 1} frames {start_frame}-{end_frame}")
        diagnostic_windows.append((label, start_frame, end_frame))
        recovered = scan_window(
            video_path,
            start_frame,
            end_frame,
            {key: remaining},
            all_boxes,
            artifact_dir,
            label,
            args.save_images,
            args.full_frame_stride,
            args.frame_stride,
        )
        for recovered_key, hits in recovered.items():
            for index0, hit in sorted(hits.items()):
                if index0 in state_chunks.setdefault(recovered_key, set()):
                    continue
                write_chunk_file(state_dir, hit)
                state_chunks[recovered_key].add(index0)
                totals.setdefault(recovered_key, hit.total)
                target_missing.setdefault(recovered_key, set()).discard(index0)
                recovered_total += 1
                recovered_report.append(
                    {
                        "file_name": hit.file_name,
                        "chunk": hit.index1,
                        "frame": hit.frame_no,
                    }
                )
                print(f"[RECOVERED] {hit.file_name} chunk {hit.index1}/{hit.total} at frame {hit.frame_no}")

    if args.save_images:
        save_missing_window_images(video_path, diagnostic_windows, all_boxes, artifact_dir)

    save_state_chunks(state_dir, state_chunks, totals)
    refresh_missing_reports(state_dir, state_chunks, totals)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "video": str(video_path),
        "state_dir": str(state_dir),
        "recovered_count": recovered_total,
        "recovered": recovered_report,
        "remaining": {
            f"{key[0]}:{key[1]}": len([index for index in range(totals[key]) if index not in state_chunks.get(key, set())])
            for key in totals
        },
    }
    write_json(artifact_dir / "supplemental_scan_report.json", report)

    print(f"[INFO] Recovered chunks: {recovered_total}")
    failed = reconstruct_from_state(state_dir, output_dir)
    if failed:
        print("[WARN] Reconstruction still incomplete.")
        return 1
    print("[DONE] Reconstruction complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
