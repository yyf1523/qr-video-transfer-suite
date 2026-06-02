# QR Video Optical Transfer Scripts

这套脚本对应 Gemini 对话里的最终方案：内网 Linux 发送端把源码压缩包和 `lib/` 下的 jar 依赖编码成带红色定位框的二维码视频；外网 Windows 接收端读取手机录像，自动透视矫正、CRC32 校验、去重并重构文件。

## 离线依赖准备

有网机器先下载 wheel：

```powershell
python -m pip download -r requirements-linux-encoder.txt -d wheels-linux
python -m pip download -r requirements-windows-decoder.txt -d wheels-windows
```

内网 Linux 安装：

```bash
python3 -m pip install --no-index --find-links wheels-linux -r requirements-linux-encoder.txt
```

外网 Windows 安装：

```powershell
py -3 -m pip install --no-index --find-links wheels-windows -r requirements-windows-decoder.txt
```

## 单张图片文本剪贴板

短文本可以不走视频，直接编码成一张二维码图片；另一端解码这张图片即可拿回原文。中文、换行和特殊字符会按 UTF-8 封装。

图形界面：

```bash
# UOS / Linux
bash scripts/run_text_qr_gui.sh
```

```cmd
:: Windows 11
scripts\run_text_qr_gui.cmd
```

命令行生成图片：

```bash
python3 scripts/text_qr_clipboard.py encode "要传输的一段文本" -o text-qr.png
```

从系统剪贴板生成图片：

```bash
python3 scripts/text_qr_clipboard.py encode --clipboard -o text-qr.png
```

解码图片并输出文本：

```bash
python3 scripts/text_qr_clipboard.py decode text-qr.png
```

解码图片并复制到系统剪贴板：

```bash
python3 scripts/text_qr_clipboard.py decode text-qr.png --copy
```

单张二维码容量有限，适合配置片段、命令、错误日志、短 SQL、短 JSON 等文本；大文件仍使用后面的二维码视频传输。

## 发送端：Linux 生成视频

```bash
python3 scripts/linux_encoder_hd.py --source source.zip --lib-dir lib -o hd_secure_stream.mp4
```

### 发送端图形界面：UOS / Linux

UOS 统信 Linux 上可以启动加码端图形界面：

```bash
bash scripts/run_encoder_gui.sh
```

或直接运行：

```bash
python3 scripts/linux_encoder_gui.py
```

如果系统提示缺少 `tkinter`，先安装系统包，例如 Debian/UOS 系：

```bash
sudo apt install python3-tk
```

界面里选择源码压缩包、添加一个或多个 jar 目录，预设建议：

- `本机 1080p 横向 4QR`：输出 `1920x1080` 白色画布，一行横向拼接 4 个 QR，适合本机 1080p 全屏播放录屏，减少播放器黑边。
- `电脑录屏 2x2 高密度模式`：一帧放 4 个二维码，使用二进制载荷协议，适合电脑屏幕录制，速度明显高于单 QR 模式。
- `电脑录屏 60fps 高速模式`：每个二维码显示 5 帧，60 FPS 播放，适合高刷新率屏幕 + 60 FPS 录屏。
- `电脑录屏 1080p 适配`：适合 1920x1080 屏幕完整显示，也可在 2560x1600 屏幕放大播放。
- `电脑录屏 2.5K/1600 高度`：适合 2560x1600 屏幕录制，二维码更大。
- `手机拍屏稳妥模式`：更慢，但对手机拍屏容错更高。

内网一键脚本会自动创建 venv、优先使用本目录 `wheels-linux/` 离线 wheel、扫描当前目录：

- 根目录必须有且仅有一个源码压缩包：`*.zip`、`*.tar.gz`、`*.tgz`、`*.tar` 或 `*.7z`。
- 自动扫描 `lib/`、`libs/`、`dependency/`、`dependencies/`、`deps/`、`jars/` 下的 jar。
- 自动扫描当前目录四层以内的 npm 包归档：`*.tgz`、`*.npm`。
- 如果没有 npm 包归档但存在 `node_modules/`，默认打包成 `qr-video-out/npm-packages.tgz` 一并编码。

```bash
bash scripts/encode_auto.sh
```

常用环境变量：

```bash
OUT_DIR=./qr-video-out INCLUDE_NODE_MODULES=0 bash scripts/encode_auto.sh
RESUME_MISSING=decode_state/missing_chunks.json bash scripts/encode_auto.sh
ENCODER_EXTRA_ARGS="--passes 4 --repeat 5" bash scripts/encode_auto.sh
ENCODER_EXTRA_ARGS="--target-kbps 0.2 --repeat 4 --fps 3" bash scripts/encode_auto.sh
```

电脑录屏高速模式推荐：

```bash
ENCODER_EXTRA_ARGS="--qr-version 40 --box-size 4 --chunk-size 850 --fps 60 --repeat 5 --passes 1 --label-height 112 --meta-qr-size 96 --meta-qr-version 6 --color-border 36 --outer-white 18" bash scripts/encode_auto.sh
```

这个模式下每个二维码连续显示 5 帧，60 FPS 播放时每秒约 12 个不同分片；按 `850` 字节/片计算，理论净载荷约 `10 KiB/s`。

电脑录屏 2x2 高密度模式推荐：

```bash
ENCODER_EXTRA_ARGS="--payload-mode binary --qr-error-correction H --qr-version 40 --box-size 2 --chunk-size 1200 --fps 60 --repeat 5 --passes 1 --grid-cols 2 --grid-rows 2 --grid-gap 16 --label-height 84 --label-scale 0.55 --label-thickness 1 --meta-qr-size 64 --meta-qr-version 6 --meta-qr-box-size 2 --color-border 20 --outer-white 10" bash scripts/encode_auto.sh
```

这个模式一帧包含 4 个 QR，并且用二进制协议替代 ASCII/base64 协议。按 `1200` 字节/片、`60 FPS`、每组显示 `5` 帧估算，有效数据段理论净载荷约 `56 KiB/s`；完整视频还包含默认 3 秒白头和 3 秒黑尾，所以短文件按总时长计算会低一些。

本机 1080p 横向 4QR 推荐：

```bash
ENCODER_EXTRA_ARGS="--payload-mode binary --qr-error-correction H --qr-version 40 --box-size 2 --chunk-size 1200 --fps 60 --repeat 5 --passes 1 --grid-cols 4 --grid-rows 1 --grid-gap 10 --canvas-width 1920 --canvas-height 1080 --label-height 64 --label-scale 0.45 --label-thickness 1 --meta-qr-size 48 --meta-qr-version 6 --meta-qr-box-size 2 --color-border 8 --outer-white 4" bash scripts/encode_auto.sh
```

这个模式视频本身就是 `1920x1080`，背景纯白，QR 横向居中排列，红框和外白边更窄，适合全屏播放时减少黑边。如果录屏区域不是 1080p，也可以把 `--canvas-width/--canvas-height` 改成录屏区域尺寸，例如 `1080x1028`。

编码视频默认带纯色同步段：

- 开头：纯白 3 秒，用于录屏开始缓冲。
- 结尾：纯黑 3 秒，用于录屏结束标记。

视频帧顶部会包含两个辅助标记：

- 左上角小二维码：只放分片元数据（文件、序号、总数、CRC、长度），用于快速定位当前分片。
- 右上角数字：给人工/OCR 看，格式为 `000001/056331`。

也可以直接传多个文件或目录：

```bash
python3 scripts/linux_encoder_hd.py source.zip lib -o hd_secure_stream.mp4 --passes 3 --repeat 4
```

默认参数偏向准确率：QR 版本 20、H 级纠错、180 字节/片、6 FPS、每片重复 3 次、整体重复 2 轮。视频开头默认纯白 3 秒，结尾默认纯黑 3 秒。视频会更大更慢，但接收端会用 CRC32 拒绝任何 bit 错误的分片。

发送端会同时输出分片校验文件：

- `hd_secure_stream.manifest.md`：给人排查用，包含每个源码包、每个 jar、每个分片的大小、CRC32 和 SHA256。
- `hd_secure_stream.manifest.json`：给接收端预加载完整文件清单用，能识别“某个 jar 一个分片都没扫到”的情况。

## 接收端：Windows 解手机录像或屏幕录像

```powershell
py -3 scripts\win_decoder_hd.py phone_record.mp4 -o reconstructed_project
```

### 接收端图形界面：Windows 11

Windows 11 上可以双击或在终端运行：

```cmd
scripts\run_decoder_gui.cmd
```

或直接运行：

```powershell
python scripts\win_decoder_gui.py
```

界面支持两种流程：

- 选择已有录屏视频后解码。
- 勾选“先录屏”，由解码端录制桌面视频，录完后自动解码。

如果用电脑录屏，不需要额外录屏软件，解码端可以先录屏再自动解码：

```powershell
py -3 scripts\win_decoder_hd.py --record-screen --record-seconds 30 --screen-mode `
  --record-output screen_record.mp4 `
  --manifest-json hd_secure_stream.manifest.json `
  -o reconstructed_project
```

如果加码端使用 `60 FPS + repeat 5`，解码端使用固定 60 FPS 录屏与每 5 帧抽样：

```powershell
py -3 scripts\win_decoder_hd.py --record-screen --record-seconds 30 --screen-60fps `
  --record-output screen_record.mp4 `
  --manifest-json hd_secure_stream.manifest.json `
  -o reconstructed_project
```

如果已经有录屏文件：

```powershell
py -3 scripts\win_decoder_hd.py screen_record.mp4 --screen-60fps `
  --manifest-json hd_secure_stream.manifest.json `
  -o reconstructed_project
```

如果加码端使用 `2x2 高密度模式`，解码端使用多码网格模式，默认逐帧扫全帧里的多个 QR：

```powershell
py -3 scripts\win_decoder_hd.py screen_record.mp4 --screen-grid `
  --manifest-json hd_secure_stream.manifest.json `
  -o reconstructed_project
```

只录屏、不解码：

```powershell
py -3 scripts\win_decoder_hd.py --record-screen --record-seconds 30 --record-only `
  --record-output screen_record.mp4
```

录屏参数：

- `--record-monitor 1`：录第一个显示器；`0` 表示录所有显示器。
- `--record-region left,top,width,height`：只录指定区域，例如 `--record-region 100,80,1280,720`。
- `--screen-mode`：适合直接视频/电脑录屏，会自动使用较快的解码参数。

如果在 Linux/WSL 或 Git Bash 里解码，也可以使用一键脚本：

```bash
MANIFEST_JSON=hd_secure_stream.manifest.json bash scripts/decode_auto.sh phone_record.mp4 reconstructed_project
```

输出结构：

```text
reconstructed_project\
  source.zip
  lib\
    xxx.jar
```

## 断点续传

接收端默认启用续传状态目录 `decode_state`。每次解码成功且通过 CRC32 的分片都会保存下来；下一次解另一个手机录像时，只要继续使用同一个 `--state-dir`，脚本会自动加载旧分片继续补齐。

```powershell
py -3 scripts\win_decoder_hd.py first_record.mp4 -o reconstructed_project --state-dir decode_state --manifest-json hd_secure_stream.manifest.json
py -3 scripts\win_decoder_hd.py second_record.mp4 -o reconstructed_project --state-dir decode_state --manifest-json hd_secure_stream.manifest.json
```

如果仍有缺片，接收端会输出：

```text
decode_state\
  state.json
  missing_chunks.json
  missing_report.md
  chunks\
```

把 `missing_chunks.json` 带回内网 Linux，在同一批输入文件不变的前提下生成补片视频：

```bash
python3 scripts/linux_encoder_hd.py --source source.zip --lib-dir lib \
  --resume-missing missing_chunks.json \
  -o repair_stream.mp4
```

再用 Windows 端继续解补片录像：

```powershell
py -3 scripts\win_decoder_hd.py repair_record.mp4 -o reconstructed_project --state-dir decode_state
```

不同 jar 包按 `file_type + lib 内相对路径` 独立记录。例如 `lib/a/x.jar` 和 `lib/b/x.jar` 会分别续传，不会只按 basename 混在一起。

## 录像建议

- 手机尽量正对屏幕，开始录制前长按画面锁定对焦和曝光。
- 距离屏幕 50cm 到 1m，必要时用 2x 或 3x 变焦，减少摩尔纹。
- 屏幕亮度建议 50% 到 70%，避免白底过曝。
- 如果缺片，优先增加发送端 `--passes` 和 `--repeat`，例如 `--passes 4 --repeat 5`。
- 解码端会尝试红框透视校正、降采样再放大、CLAHE 对比度增强、锐化、开运算和自适应二值化，以降低手机拍屏的摩尔纹影响。
- 解码端会优先尝试 `zxing-cpp`，再尝试 OpenCV QRCodeDetector，最后使用 pyzbar/zbar；哪个引擎能解就用哪个。
- 解码端默认开启连续帧多数投票：同一分片重复播放时，会把连续多帧拉平并二值化，按像素投票重建更干净的二维码。可用 `--vote-window 7 --vote-min-frames 4` 调大窗口，或 `--vote-window 1` 关闭。
- 解码端可用 `--confirm-copies 2` 要求同一分片至少两次解出相同 payload CRC 才入库；如果两次不一致，就继续等下一帧，最终缺失的分片走续传补片。
