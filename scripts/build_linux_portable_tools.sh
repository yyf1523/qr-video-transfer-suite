#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_root="${PYTHON_ROOT:-$HOME/.local/share/uv/python/cpython-3.11.15-linux-x86_64-gnu}"
python_bin="$python_root/bin/python3.11"
output_dir="$project_root/build/electron-tools/linux-x64"
new_dir="$project_root/build/electron-tools/linux-x64.new"

if [[ ! -x "$python_bin" ]]; then
  echo "Missing portable Python 3.11: $python_bin" >&2
  echo "Install it with: ~/.local/bin/uv python install 3.11" >&2
  exit 1
fi

rm -rf "$new_dir"
mkdir -p "$new_dir/python-runtime" "$new_dir/python-packages" "$new_dir/scripts"

cp -aL "$python_root/bin" "$new_dir/python-runtime/"
cp -aL "$python_root/lib" "$new_dir/python-runtime/"
cp -aL "$python_root/include" "$new_dir/python-runtime/"
cp -aL "$python_root/pyvenv.cfg" "$new_dir/python-runtime/" 2>/dev/null || true
cp "$project_root/scripts/linux_encoder_hd.py" "$new_dir/scripts/linux_encoder_hd.py"
cp "$project_root/scripts/win_decoder_hd.py" "$new_dir/scripts/win_decoder_hd.py"
cp "$project_root/scripts/text_qr_clipboard.py" "$new_dir/scripts/text_qr_clipboard.py"

"$HOME/.local/bin/uv" pip install --python "$python_bin" --target "$new_dir/python-packages" \
  "numpy==1.26.4" \
  "opencv-python-headless==4.10.0.84" \
  "imageio-ffmpeg==0.6.0" \
  "Pillow==10.4.0" \
  "qrcode==7.4.2" \
  "zxing-cpp==2.3.0" \
  "mss==9.0.2" \
  "pyzbar==0.1.9"

write_launcher() {
  local name="$1"
  local script="$2"
  cat > "$new_dir/$name" <<SH
#!/usr/bin/env sh
set -eu
DIR=\$(CDPATH= cd -- "\$(dirname -- "\$0")" && pwd)
LIB_PATHS="\$DIR/python-runtime/lib"
for libdir in "\$DIR"/python-packages/*.libs; do
  [ -d "\$libdir" ] && LIB_PATHS="\$libdir:\$LIB_PATHS"
done
export PYTHONHOME="\$DIR/python-runtime"
export PYTHONPATH="\$DIR/python-packages:\$DIR/scripts\${PYTHONPATH:+:\$PYTHONPATH}"
export LD_LIBRARY_PATH="\$LIB_PATHS\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
export QT_QPA_PLATFORM=offscreen
exec "\$DIR/python-runtime/bin/python3.11" "\$DIR/scripts/$script" "\$@"
SH
  chmod +x "$new_dir/$name"
}

write_launcher "QRVideoEncoderCLI" "linux_encoder_hd.py"
write_launcher "QRVideoDecoderCLI" "win_decoder_hd.py"
write_launcher "QRTextClipboardCLI" "text_qr_clipboard.py"
chmod +x "$new_dir/python-runtime/bin/python3.11"

find "$new_dir" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$new_dir" -type d \( -name "tests" -o -name "test" \) -prune -exec rm -rf {} +

"$new_dir/QRVideoEncoderCLI" --help >/tmp/qr-video-encoder-help.txt
"$new_dir/QRVideoDecoderCLI" --help >/tmp/qr-video-decoder-help.txt
"$new_dir/QRTextClipboardCLI" --help >/tmp/qr-text-help.txt

rm -rf "$output_dir.previous"
if [[ -d "$output_dir" ]]; then
  mv "$output_dir" "$output_dir.previous"
fi
mv "$new_dir" "$output_dir"

du -sh "$output_dir"
echo "Portable Linux tools written to: $output_dir"
