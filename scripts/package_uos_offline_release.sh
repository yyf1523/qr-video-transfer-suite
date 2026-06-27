#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_root="${RELEASE_ROOT:-/mnt/e/release/源码视频加码解码器/20260625-v2}"
package_name="${PACKAGE_NAME:-QRVideoTransfer-UOS-full-offline-20260625}"
stage_dir="$project_root/build/package-staging/$package_name"
appimage="${APPIMAGE:-$project_root/build/electron-dist/QRVideoTransferSuite-2026.6.24-linux-x86_64.AppImage}"
appimage_name="$(basename "$appimage")"
linux_unpacked="$project_root/build/electron-dist/linux-unpacked"

if [[ ! -f "$appimage" ]]; then
  echo "Missing AppImage: $appimage" >&2
  exit 1
fi

if [[ ! -d "$linux_unpacked" ]]; then
  echo "Missing linux-unpacked: $linux_unpacked" >&2
  exit 1
fi

rm -rf "$stage_dir"
mkdir -p "$stage_dir"
cp -a "$linux_unpacked" "$stage_dir/linux-unpacked"
cp "$appimage" "$stage_dir/$appimage_name"
chmod +x "$stage_dir/$appimage_name"
mkdir -p "$stage_dir/runtime-data/share/icons/hicolor"
cp -a /usr/share/mime "$stage_dir/runtime-data/share/mime"
cp -a /usr/share/icons/hicolor/index.theme "$stage_dir/runtime-data/share/icons/hicolor/index.theme"
if [[ -d /usr/share/icons/Adwaita ]]; then
  cp -a /usr/share/icons/Adwaita "$stage_dir/runtime-data/share/icons/Adwaita"
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
  pixbuf_dest="$stage_dir/runtime-data/lib/gdk-pixbuf-2.0/$pixbuf_version"
  mkdir -p "$pixbuf_dest"
  cp -a "$pixbuf_loader_dir" "$pixbuf_dest/loaders"
  pixbuf_cache="$(dirname "$pixbuf_loader_dir")/loaders.cache"
  if [[ -f "$pixbuf_cache" ]]; then
    sed "s|$pixbuf_loader_dir|__GDK_PIXBUF_LOADER_DIR__|g" "$pixbuf_cache" > "$pixbuf_dest/loaders.cache.template"
  fi
fi

cat > "$stage_dir/Start-UOS-AppImage.sh" <<'SH'
#!/usr/bin/env sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export ELECTRON_DISABLE_GPU=1
export LIBGL_ALWAYS_SOFTWARE=1
export XDG_DATA_DIRS="$DIR/runtime-data/share:/usr/local/share:/usr/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
export GTK_DATA_PREFIX="$DIR/runtime-data"
export GTK_EXE_PREFIX="$DIR/runtime-data"
unset APPIMAGE_EXTRACT_AND_RUN
for pixbuf_dir in "$DIR"/runtime-data/lib/gdk-pixbuf-2.0/*; do
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
  --disable-dev-shm-usage \
  --ozone-platform=x11 \
  --log-level=3 \
  "$@"
SH

cat > "$stage_dir/Start-UOS-unpacked.sh" <<'SH'
#!/usr/bin/env sh
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export ELECTRON_DISABLE_GPU=1
export LIBGL_ALWAYS_SOFTWARE=1
export XDG_DATA_DIRS="$DIR/runtime-data/share:/usr/local/share:/usr/share${XDG_DATA_DIRS:+:$XDG_DATA_DIRS}"
export GTK_DATA_PREFIX="$DIR/runtime-data"
export GTK_EXE_PREFIX="$DIR/runtime-data"
for pixbuf_dir in "$DIR"/runtime-data/lib/gdk-pixbuf-2.0/*; do
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
chmod +x "$DIR/linux-unpacked/qr-video-transfer-suite" 2>/dev/null || true
chmod +x "$DIR/linux-unpacked/resources/tools/linux-x64/QRVideoEncoderCLI" 2>/dev/null || true
chmod +x "$DIR/linux-unpacked/resources/tools/linux-x64/QRVideoDecoderCLI" 2>/dev/null || true
chmod +x "$DIR/linux-unpacked/resources/tools/linux-x64/QRTextClipboardCLI" 2>/dev/null || true
exec "$DIR/linux-unpacked/qr-video-transfer-suite" \
  --no-sandbox \
  --disable-gpu \
  --disable-gpu-compositing \
  --disable-gpu-rasterization \
  --disable-dev-shm-usage \
  --ozone-platform=x11 \
  --log-level=3 \
  "$@"
SH
chmod +x "$stage_dir/Start-UOS-AppImage.sh" "$stage_dir/Start-UOS-unpacked.sh"

cat > "$stage_dir/README-UOS-offline.txt" <<'TXT'
QRVideoTransferSuite UOS/Linux full offline package

Target: UOS / Debian 10.10 x86_64 cloud desktop.
Default Linux performance: --workers 4 --memory-gb 6.

This package is self-contained:
- Electron runtime is in linux-unpacked/ and in the AppImage.
- Python 3.11 runtime is bundled in linux-unpacked/resources/tools/linux-x64/python-runtime/.
- Python dependencies are bundled in linux-unpacked/resources/tools/linux-x64/python-packages/.
- ffmpeg is bundled through imageio-ffmpeg for UOS-compatible H.264 MP4 output.
- No system Python is required. Debian 10.10 may only have Python 2.7; that is OK.

Recommended launch:
  ./Start-UOS-AppImage.sh

Fallback launch when AppImage/FUSE is blocked:
  ./Start-UOS-unpacked.sh

Both launchers force software rendering for cloud desktops:
ELECTRON_DISABLE_GPU=1, LIBGL_ALWAYS_SOFTWARE=1, --disable-gpu, --ozone-platform=x11.

The launchers also restore GTK icon/MIME lookup paths and a bundled
gdk-pixbuf loader cache. Use the launchers instead of starting the binary
directly to avoid pixbuf/icon crashes on minimal cloud desktops.
TXT

mkdir -p "$release_root"
tar -C "$project_root/build/package-staging" -czf "$release_root/$package_name.tar.gz" "$package_name"
ls -lh "$release_root/$package_name.tar.gz"
