#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
linux_unpacked="$project_root/build/electron-dist/linux-unpacked"
appimagetool="$project_root/build/tools/appimagetool-x86_64.AppImage"
appdir="$project_root/build/electron-appimage/QRVideoTransferSuite.AppDir"
icon_png="$project_root/electron-shell/assets/app-icon-256.png"
output="${1:-$project_root/build/electron-dist/QRVideoTransferSuite-2026.6.24-linux-x86_64.AppImage}"

if [[ ! -d "$linux_unpacked" ]]; then
  echo "Missing linux-unpacked directory: $linux_unpacked" >&2
  exit 1
fi

if [[ ! -f "$appimagetool" ]]; then
  echo "Missing appimagetool: $appimagetool" >&2
  exit 1
fi

if [[ ! -f "$icon_png" ]]; then
  echo "Missing app icon: $icon_png" >&2
  exit 1
fi

rm -rf "$appdir"
mkdir -p "$appdir/usr/bin" "$appdir/usr/share/applications" "$appdir/usr/share/icons/hicolor/256x256/apps"
cp -a "$linux_unpacked" "$appdir/usr/bin/QRVideoTransferSuite"
cp -a /usr/share/mime "$appdir/usr/share/mime"
cp -a /usr/share/icons/hicolor/index.theme "$appdir/usr/share/icons/hicolor/index.theme"
if [[ -d /usr/share/icons/Adwaita ]]; then
  cp -a /usr/share/icons/Adwaita "$appdir/usr/share/icons/Adwaita"
fi
pixbuf_loader_dir=""
for loader_dir in \
  /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/*/loaders \
  /usr/lib64/gdk-pixbuf-2.0/*/loaders; do
  if [[ -d "$loader_dir" ]]; then
    pixbuf_loader_dir="$loader_dir"
    break
  fi
done
if [[ -n "$pixbuf_loader_dir" ]]; then
  pixbuf_version="$(basename "$(dirname "$pixbuf_loader_dir")")"
  pixbuf_dest="$appdir/usr/lib/gdk-pixbuf-2.0/$pixbuf_version"
  mkdir -p "$pixbuf_dest"
  cp -a "$pixbuf_loader_dir" "$pixbuf_dest/loaders"
  pixbuf_cache="$(dirname "$pixbuf_loader_dir")/loaders.cache"
  if [[ -f "$pixbuf_cache" ]]; then
    sed "s|$pixbuf_loader_dir|__GDK_PIXBUF_LOADER_DIR__|g" "$pixbuf_cache" > "$pixbuf_dest/loaders.cache.template"
  fi
fi

cat > "$appdir/AppRun" <<'APPRUN'
#!/usr/bin/env sh
APPDIR="$(dirname "$(readlink -f "$0")")"
export ELECTRON_DISABLE_GPU=1
export LIBGL_ALWAYS_SOFTWARE=1
export LIBVA_MESSAGING_LEVEL="${LIBVA_MESSAGING_LEVEL:-0}"
export LIBVA_DRIVER_NAME="${LIBVA_DRIVER_NAME:-dummy}"
export GST_VAAPI_ALL_DRIVERS="${GST_VAAPI_ALL_DRIVERS:-0}"
if [ -z "${QR_SUITE_LOG_DIR:-}" ]; then
  if [ -n "${APPIMAGE:-}" ]; then
    export QR_SUITE_LOG_DIR="$(dirname "$APPIMAGE")/logs"
  else
    export QR_SUITE_LOG_DIR="$APPDIR/logs"
  fi
fi
mkdir -p "$QR_SUITE_LOG_DIR" 2>/dev/null || export QR_SUITE_LOG_DIR="${TMPDIR:-/tmp}/qr-video-transfer-logs"
mkdir -p "$QR_SUITE_LOG_DIR" 2>/dev/null || true
launcher_log="$QR_SUITE_LOG_DIR/launcher-$(date +%Y%m%d-%H%M%S).log"
export XDG_DATA_DIRS="$APPDIR/usr/share:/usr/local/share:/usr/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
export GTK_DATA_PREFIX="$APPDIR/usr"
export GTK_EXE_PREFIX="$APPDIR/usr"
for pixbuf_dir in "$APPDIR"/usr/lib/gdk-pixbuf-2.0/*; do
  if [ -d "$pixbuf_dir/loaders" ]; then
    export GDK_PIXBUF_MODULEDIR="$pixbuf_dir/loaders"
    if [ -r "$pixbuf_dir/loaders.cache.template" ]; then
      pixbuf_cache="${TMPDIR:-/tmp}/qr-video-transfer-gdk-pixbuf-$$.cache"
      sed "s#__GDK_PIXBUF_LOADER_DIR__#$GDK_PIXBUF_MODULEDIR#g" "$pixbuf_dir/loaders.cache.template" > "$pixbuf_cache"
      export GDK_PIXBUF_MODULE_FILE="$pixbuf_cache"
    elif [ -r "$pixbuf_dir/loaders.cache" ]; then
      export GDK_PIXBUF_MODULE_FILE="$pixbuf_dir/loaders.cache"
    fi
    break
  fi
done
if [ -z "${GDK_PIXBUF_MODULE_FILE:-}" ]; then
  for cache in \
    /usr/lib/x86_64-linux-gnu/gdk-pixbuf-2.0/*/loaders.cache \
    /usr/lib64/gdk-pixbuf-2.0/*/loaders.cache; do
    if [ -r "$cache" ]; then
      export GDK_PIXBUF_MODULE_FILE="$cache"
      break
    fi
  done
fi
exec "$APPDIR/usr/bin/QRVideoTransferSuite/qr-video-transfer-suite" \
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
APPRUN
chmod +x "$appdir/AppRun"

cat > "$appdir/QRVideoTransferSuite.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=QR Video Transfer Suite
Exec=QRVideoTransferSuite
Icon=qr-video-transfer-suite
Categories=Utility;
Terminal=false
DESKTOP
cp "$appdir/QRVideoTransferSuite.desktop" "$appdir/usr/share/applications/QRVideoTransferSuite.desktop"

cp "$icon_png" "$appdir/qr-video-transfer-suite.png"
cp "$appdir/qr-video-transfer-suite.png" "$appdir/.DirIcon"
cp "$appdir/qr-video-transfer-suite.png" "$appdir/usr/share/icons/hicolor/256x256/apps/qr-video-transfer-suite.png"

mkdir -p "$(dirname "$output")"
chmod +x "$appimagetool"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$appimagetool" "$appdir" "$output"
chmod +x "$output"
echo "$output"
