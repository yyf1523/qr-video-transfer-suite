#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIDEO_PATH="${1:?Usage: scripts/decode_auto.sh <recorded_video> [output_dir]}"
OUTPUT_DIR="${2:-$PWD/reconstructed_project}"
STATE_DIR="${STATE_DIR:-$PWD/decode_state}"
WHEELS_DIR="${WHEELS_DIR:-$ROOT_DIR/wheels-windows}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-decoder}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  if "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    :
  else
    rm -rf "$VENV_DIR"
    echo "[WARN] python venv is unavailable; falling back to current user Python site-packages." >&2
  fi
fi

if [ -x "$VENV_DIR/bin/python" ] && "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
  RUN_PY="$VENV_DIR/bin/python"
  PIP_ARGS=()
else
  RUN_PY="$PYTHON_BIN"
  if ! "$RUN_PY" -m pip --version >/dev/null 2>&1; then
    echo "[ERROR] pip is unavailable. Install python3-venv or run get-pip.py for this user first." >&2
    exit 2
  fi
  PIP_ARGS=(--user --break-system-packages)
fi

if [ -d "$WHEELS_DIR" ] && find "$WHEELS_DIR" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) | grep -q .; then
  "$RUN_PY" -m pip install "${PIP_ARGS[@]}" --no-index --find-links "$WHEELS_DIR" -r "$ROOT_DIR/requirements-windows-decoder.txt"
else
  "$RUN_PY" -m pip install "${PIP_ARGS[@]}" -r "$ROOT_DIR/requirements-windows-decoder.txt"
fi

MANIFEST_ARGS=()
if [ -n "${MANIFEST_JSON:-}" ]; then
  MANIFEST_ARGS=(--manifest-json "$MANIFEST_JSON")
fi

"$RUN_PY" "$ROOT_DIR/scripts/win_decoder_hd.py" \
  "$VIDEO_PATH" \
  -o "$OUTPUT_DIR" \
  --state-dir "$STATE_DIR" \
  "${MANIFEST_ARGS[@]}" \
  ${DECODER_EXTRA_ARGS:-}

echo "Output: $OUTPUT_DIR"
echo "State: $STATE_DIR"
