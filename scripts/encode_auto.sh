#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK_DIR="${1:-$(pwd)}"
OUT_DIR="${OUT_DIR:-$WORK_DIR/qr-video-out}"
WHEELS_DIR="${WHEELS_DIR:-$ROOT_DIR/wheels-linux}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-linux-encoder}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$OUT_DIR"

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
  "$RUN_PY" -m pip install "${PIP_ARGS[@]}" --no-index --find-links "$WHEELS_DIR" -r "$ROOT_DIR/requirements-linux-encoder.txt"
else
  "$RUN_PY" -m pip install "${PIP_ARGS[@]}" -r "$ROOT_DIR/requirements-linux-encoder.txt"
fi

mapfile -t PRIMARY_ARCHIVES < <(
  find "$WORK_DIR" -maxdepth 1 -type f \( \
    -iname '*.zip' -o -iname '*.7z' -o -iname '*.tar' \
  \) ! -iname 'npm-packages.tgz' | sort
)

if [ "${#PRIMARY_ARCHIVES[@]}" -eq 1 ]; then
  ARCHIVES=("${PRIMARY_ARCHIVES[0]}")
else
  mapfile -t ARCHIVES < <(
  find "$WORK_DIR" -maxdepth 1 -type f \( \
    -iname '*.zip' -o -iname '*.tar.gz' -o -iname '*.tgz' -o -iname '*.tar' -o -iname '*.7z' \
  \) ! -iname 'npm-packages.tgz' | sort
)
fi

if [ "${#ARCHIVES[@]}" -ne 1 ]; then
  printf 'Expected exactly one source archive in %s, found %s:\n' "$WORK_DIR" "${#ARCHIVES[@]}" >&2
  printf '  %s\n' "${ARCHIVES[@]:-<none>}" >&2
  exit 2
fi

SOURCE_ARCHIVE="${ARCHIVES[0]}"
INPUTS=(--source "$SOURCE_ARCHIVE")

for dir_name in lib libs dependency dependencies deps jars; do
  if [ -d "$WORK_DIR/$dir_name" ] && find "$WORK_DIR/$dir_name" -type f -iname '*.jar' | grep -q .; then
    INPUTS+=(--lib-dir "$WORK_DIR/$dir_name")
  fi
done

NPM_BUNDLE="$OUT_DIR/npm-packages.tgz"
mapfile -t NPM_TARBALLS < <(
  find "$WORK_DIR" -maxdepth 4 -type f \( -iname '*.tgz' -o -iname '*.npm' \) \
    ! -path "$OUT_DIR/*" ! -name "$(basename "$SOURCE_ARCHIVE")" | sort
)

if [ "${#NPM_TARBALLS[@]}" -gt 0 ]; then
  for npm_pkg in "${NPM_TARBALLS[@]}"; do
    INPUTS+=("$npm_pkg")
  done
elif [ "${INCLUDE_NODE_MODULES:-1}" = "1" ] && [ -d "$WORK_DIR/node_modules" ]; then
  tar -C "$WORK_DIR" \
    --exclude='node_modules/.cache' \
    --exclude='node_modules/.vite' \
    --exclude='node_modules/.pnpm-store' \
    -czf "$NPM_BUNDLE" node_modules
  INPUTS+=("$NPM_BUNDLE")
fi

if [ -n "${RESUME_MISSING:-}" ]; then
  INPUTS+=(--resume-missing "$RESUME_MISSING")
fi

OUTPUT_VIDEO="${OUTPUT_VIDEO:-$OUT_DIR/hd_secure_stream.mp4}"
MANIFEST_MD="${MANIFEST_MD:-$OUT_DIR/hd_secure_stream.manifest.md}"
MANIFEST_JSON="${MANIFEST_JSON:-$OUT_DIR/hd_secure_stream.manifest.json}"

"$RUN_PY" "$ROOT_DIR/scripts/linux_encoder_hd.py" \
  "${INPUTS[@]}" \
  -o "$OUTPUT_VIDEO" \
  --manifest "$MANIFEST_MD" \
  --manifest-json "$MANIFEST_JSON" \
  ${ENCODER_EXTRA_ARGS:-}

echo "Video: $OUTPUT_VIDEO"
echo "Manifest MD: $MANIFEST_MD"
echo "Manifest JSON: $MANIFEST_JSON"
