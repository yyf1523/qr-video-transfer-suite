#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_root="${RELEASE_ROOT:-/mnt/e/release/源码视频加码解码器/20260628-v2}"
package_name="${PACKAGE_NAME:-QRVideoTransfer-UOS-AppImage-offline-20260628-v2}"
stage_dir="$project_root/build/package-staging/$package_name"
appimage="${APPIMAGE:-$project_root/build/electron-dist/QRVideoTransferSuite-2026.6.28-hotfix.1-linux-x86_64.AppImage}"
appimage_name="$(basename "$appimage")"

if [[ ! -f "$appimage" ]]; then
  echo "Missing AppImage: $appimage" >&2
  exit 1
fi

rm -rf "$stage_dir"
mkdir -p "$stage_dir"
cp "$appimage" "$stage_dir/$appimage_name"
chmod +x "$stage_dir/$appimage_name"

cat > "$stage_dir/Start-UOS-AppImage.sh" <<'SH'
#!/usr/bin/env sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export ELECTRON_DISABLE_GPU=1
export LIBGL_ALWAYS_SOFTWARE=1
export LIBVA_MESSAGING_LEVEL="${LIBVA_MESSAGING_LEVEL:-0}"
export LIBVA_DRIVER_NAME="${LIBVA_DRIVER_NAME:-dummy}"
export GST_VAAPI_ALL_DRIVERS="${GST_VAAPI_ALL_DRIVERS:-0}"
export QR_SUITE_LOG_DIR="${QR_SUITE_LOG_DIR:-$DIR/logs}"
mkdir -p "$QR_SUITE_LOG_DIR" 2>/dev/null || export QR_SUITE_LOG_DIR="${TMPDIR:-/tmp}/qr-video-transfer-logs"
mkdir -p "$QR_SUITE_LOG_DIR" 2>/dev/null || true
launcher_log="$QR_SUITE_LOG_DIR/launcher-$(date +%Y%m%d-%H%M%S).log"
appimage_path=""
for candidate in "$DIR"/QRVideoTransferSuite-*-linux-x86_64.AppImage "$DIR"/*.AppImage; do
  if [ -f "$candidate" ]; then
    appimage_path="$candidate"
    break
  fi
done
if [ -z "$appimage_path" ]; then
  echo "No AppImage found next to launcher." >&2
  exit 1
fi
chmod +x "$appimage_path" 2>/dev/null || true
exec "$appimage_path" \
  --no-sandbox \
  --disable-gpu \
  --disable-gpu-compositing \
  --disable-gpu-rasterization \
  --disable-accelerated-video-decode \
  --disable-accelerated-video-encode \
  --disable-dev-shm-usage \
  --ozone-platform=x11 \
  --disable-features=UseOzonePlatform,Vulkan,VaapiVideoDecoder,VaapiVideoEncoder,VaapiIgnoreDriverChecks \
  --log-level=3 \
  "$@" >> "$launcher_log" 2>&1
SH
chmod +x "$stage_dir/Start-UOS-AppImage.sh"

cat > "$stage_dir/README-UOS-AppImage.txt" <<'TXT'
QRVideoTransferSuite UOS AppImage offline package

Run:
  ./Start-UOS-AppImage.sh

Logs:
  logs/launcher-*.log
  logs/qr-video-transfer-*.log

This package uses the built-in Linux file picker and does not require
zenity/kdialog. If AppImage/FUSE is blocked on a locked-down desktop, use
the full offline package with Start-UOS-unpacked.sh instead.
TXT

mkdir -p "$release_root"
tar -C "$project_root/build/package-staging" -czf "$release_root/$package_name.tar.gz" "$package_name"
ls -lh "$release_root/$package_name.tar.gz"
