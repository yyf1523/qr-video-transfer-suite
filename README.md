# QR Video Transfer Suite

源码视频加码解码器：把源码及离线依赖整理成压缩包，编码成带红色定位框的 4QR 视频，在 UOS/云桌面中全屏播放，再由接收端录屏解码还原压缩包。项目包含命令行脚本、Tk 图形界面和 Electron 播放/录制/解码工作台。

## 功能概览

- Linux/UOS 发送端：生成单个 H.264/yuv420p MP4，默认 4QR / 30FPS / repeat=2 / FEC。
- Windows/Linux 接收端：读取手机录像或屏幕录制，自动识别前置 `QVP1` 参数二维码并解码。
- Electron 工作台：集成视频加码、专用播放器、屏幕录制、录后自动解码和文本二维码。
- UOS 离线包：Release 内提供完整离线包，含 Electron、Python 3.11、依赖、ffmpeg 和软件渲染启动脚本。
- 文本二维码：支持微信直扫原文模式，也支持高容量压缩模式。

## Release 包

正式发布包在 GitHub Release 中提供：

- `QRVideoTransfer-UOS-full-offline-YYYYMMDD.tar.gz`：UOS/Linux 完整离线包，推荐云桌面直接使用。
- `QRVideoTransfer-Linux-portable-tools-YYYYMMDD.tar.gz`：Linux 便携命令行工具包。
- `SHA256SUMS.txt`：发布包校验值。

UOS 完整离线包解压后优先运行：

```bash
./Start-UOS-AppImage.sh
```

如果 AppImage/FUSE 受限，使用备用启动器：

```bash
./Start-UOS-unpacked.sh
```

UOS/云桌面入口会强制软件渲染并关闭 VAAPI/硬件视频加速探测，避免虚拟显卡环境出现 `libva error` 后影响稳定性。离线包解压目录会生成 `logs/`：

- `launcher-*.log`：启动脚本、Electron 标准输出和标准错误。
- `qr-video-transfer-*.log`：主进程、渲染进程、文件选择器、CLI 子进程和崩溃事件。

界面日志区有“打开日志目录”按钮。若选择压缩包或播放视频后闪退，优先查看这两个日志文件；如果解压目录不可写，日志会退到系统临时目录 `qr-video-transfer-logs/`。
UOS/Linux 默认使用应用内置文件选择器，不依赖系统安装 `zenity` 或 `kdialog`。如需临时回到系统原生对话框，可设置 `QR_SUITE_USE_NATIVE_DIALOG=1` 后再启动。

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

短文本可以不走视频，直接编码成一张二维码图片；另一端解码这张图片即可拿回原文。默认是“微信直扫”模式：中文、英文、换行和常见符号都按原文 UTF-8 写入二维码，微信扫码后可以直接查看和复制。

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

高容量压缩模式适合同一张图里塞更多中英文文本，但扫码后会看到 `QTXT2:` 开头的压缩载荷，必须用本工具解码：

```bash
python3 scripts/text_qr_clipboard.py encode "要传输的一段较长文本" -o text-qr.png --codec auto
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

单张二维码容量有限，适合配置片段、命令、错误日志、短 SQL、短 JSON 等文本；大文件仍使用后面的二维码视频传输。微信直扫模式可读性最好；压缩模式容量更高，但需要接收端运行本工具解码。

## 发送端：Linux 生成视频

```bash
python3 scripts/linux_encoder_hd.py --source source.zip -o hd_secure_stream.mp4
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

界面里只需选择源码压缩包。源码、Jar 依赖及其他离线依赖应先整理进同一个压缩包，预设建议：

- `本机 1080p 横向 4QR`：输出 `1920x1080` 白色画布，一行横向拼接 4 个 QR，适合本机 1080p 全屏播放录屏，减少播放器黑边。
- `电脑录屏 2x2 高密度模式`：一帧放 4 个二维码，使用二进制载荷协议，适合电脑屏幕录制，速度明显高于单 QR 模式。
- `电脑录屏 60fps 高速模式`：每个二维码显示 5 帧，60 FPS 播放，适合高刷新率屏幕 + 60 FPS 录屏。
- `电脑录屏 1080p 适配`：适合 1920x1080 屏幕完整显示，也可在 2560x1600 屏幕放大播放。
- `电脑录屏 2.5K/1600 高度`：适合 2560x1600 屏幕录制，二维码更大。
- `手机拍屏稳妥模式`：更慢，但对手机拍屏容错更高。

内网一键脚本会自动创建 venv、优先使用本目录 `wheels-linux/` 离线 wheel、扫描当前目录：

- 根目录必须有且仅有一个源码压缩包：`*.zip`、`*.tar.gz`、`*.tgz`、`*.tar` 或 `*.7z`。
- 不再接收单独 Jar 或依赖目录；依赖必须先放入源码压缩包。
- 自动扫描当前目录四层以内的 npm 包归档：`*.tgz`、`*.npm`。
- 如果没有 npm 包归档但存在 `node_modules/`，默认打包成 `qr-video-out/npm-packages.tgz` 一并编码。

```bash
bash scripts/encode_auto.sh
```

常用环境变量：

```bash
OUT_DIR=./qr-video-out INCLUDE_NODE_MODULES=0 bash scripts/encode_auto.sh
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
ENCODER_EXTRA_ARGS="--payload-mode binary --qr-error-correction H --qr-version 40 --box-size 2 --chunk-size 1100 --fps 60 --repeat 5 --passes 1 --grid-cols 2 --grid-rows 2 --grid-gap 16 --label-height 84 --label-scale 0.55 --label-thickness 1 --meta-qr-size 64 --meta-qr-version 6 --meta-qr-box-size 2 --color-border 20 --outer-white 10" bash scripts/encode_auto.sh
```

这个模式一帧包含 4 个 QR，并且用二进制协议替代 ASCII/base64 协议。按 `1100` 字节/片、`60 FPS`、每组显示 `5` 帧估算，有效数据段理论净载荷约 `52 KiB/s`；完整视频还包含默认 3 秒白头和 3 秒黑尾，所以短文件按总时长计算会低一些。

本机 1080p 横向 4QR 推荐：

```bash
ENCODER_EXTRA_ARGS="--payload-mode binary --qr-error-correction H --qr-version 40 --box-size 2 --chunk-size 1100 --fps 60 --repeat 5 --passes 1 --grid-cols 4 --grid-rows 1 --grid-gap 10 --canvas-width 1920 --canvas-height 1080 --label-height 64 --label-scale 0.45 --label-thickness 1 --meta-qr-size 48 --meta-qr-version 6 --meta-qr-box-size 2 --color-border 8 --outer-white 4" bash scripts/encode_auto.sh
```

这个模式视频本身就是 `1920x1080`，背景纯白，QR 横向居中排列，红框和外白边更窄，适合全屏播放时减少黑边。如果录屏区域不是 1080p，也可以把 `--canvas-width/--canvas-height` 改成录屏区域尺寸，例如 `1080x1028`。

本机 1080p 横向 4QR + 30FPS + FEC 推荐：

```bash
ENCODER_EXTRA_ARGS="--fast-fec-4qr" bash scripts/encode_auto.sh
```

或直接指定：

```bash
python3 scripts/linux_encoder_hd.py --source source.zip -o hd_secure_stream.mp4 --fast-fec-4qr
```

这个预设生成单个 MP4 文件，一帧横向放 4 个 QR，30 FPS、每片重复 2 帧，并按每 100 个数据分片附加 12 个 FEC 校验分片。少量缺片时，接收端会根据参数头或 manifest 自动恢复；缺口过大时本次解码会失败并输出缺失报告，需要提高 `--passes` / `--repeat` 后重新生成完整视频。
预设内置 `1100` 字节分片，给 QR v40/H 留出协议头、文件名和 FEC 元数据余量；如果手动设置的 `--chunk-size` 过大，编码器会按实际文件名自动下调，避免生成到一半出现 `QR payload does not fit`。

编码视频默认带前置参数缓冲：

- 开头：`10` 秒 `QVP1` 参数二维码缓冲，二维码里压缩写入 FPS、repeat、grid、chunk、ECC、FEC、画布尺寸、worker/内存预算等解码参数；文件清单放得下时，也会写入文件名、大小、SHA256 和总分片数。
- 可选白屏：如需额外白屏，可加 `--intro-seconds N`。
- 结尾：纯黑 3 秒，用于录屏结束标记。

MP4 播放器兼容性：

- 默认 `--mp4-profile uos`：先用 OpenCV 生成中间视频，再用随包或系统 `ffmpeg` 转成 `H.264 + yuv420p + .mp4`，适合 UOS 默认播放器。
- 快速兼容输出可加 `--mp4-profile uos-pipe`：Python 直接把 BGR 帧通过 stdin 管道写给 `ffmpeg`，省掉中间 MP4 的一次编码/解码和磁盘 IO；如果管道失败，会自动回退到默认 `uos` 两段式流程重新生成。
- 需要尝试硬件编码时可加 `--h264-encoder h264_vaapi`，可选 `--h264-hw-device /dev/dri/renderD128`。脚本会先短探测 VAAPI；不可用时直接回退到 `libx264` 软件编码，避免 `libva` 错误刷屏。
- 快速软件转码建议：`--mp4-profile uos-pipe --h264-preset ultrafast --h264-crf 18`。更稳更清晰则继续使用默认 `veryfast + crf 12`。
- 如果缺少 `ffmpeg`，脚本会保留 OpenCV 的 `mp4v` MP4 并打印警告；这种文件可以用于传输/解码，但 UOS 默认播放器可能打不开。
- 如需保留旧的 OpenCV 直写 MP4，可加 `--mp4-profile opencv`。
- Electron 图形界面的“播放与录制”页提供专用播放器和屏幕录制器，适合在 UOS/云桌面里全屏循环播放 4QR 视频，再在接收端录制并自动解码。

并发与内存预算：

- 加码端 `--workers 0` 默认自动选择最多 6 个 worker，并发渲染同一帧内的多个二维码；视频写入仍串行，保证帧顺序稳定。
- `--memory-gb 6` 会写入参数二维码，作为解码端默认排队帧内存预算。
- 如需手动固定：`--workers 6 --memory-gb 6 --param-intro-seconds 10`。

视频帧顶部会包含两个辅助标记：

- 左上角小二维码：只放分片元数据（文件、序号、总数、CRC、长度），用于快速定位当前分片。
- 右上角数字：给人工/OCR 看，格式为 `000001/056331`。

也可以直接传多个压缩包，目录、单独 Jar 和普通文件会被拒绝：

```bash
python3 scripts/linux_encoder_hd.py source.zip npm-packages.tgz -o hd_secure_stream.mp4 --passes 3 --repeat 4
```

默认参数偏向准确率：QR 版本 20、H 级纠错、180 字节/片、6 FPS、每片重复 3 次、整体重复 2 轮。视频开头默认 10 秒参数二维码，结尾默认纯黑 3 秒。视频会更大更慢，但接收端会用 CRC32 拒绝任何 bit 错误的分片。

发送端会同时输出分片校验文件：

- `hd_secure_stream.manifest.md`：给人排查用，包含每个压缩包、每个分片的大小、CRC32 和 SHA256。
- `hd_secure_stream.manifest.json`：给接收端预加载完整文件清单用；新视频会优先从前置参数二维码自动读取，旧视频或文件清单太大导致参数二维码放不下时再手工提供。

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
py -3 scripts\win_decoder_hd.py --record-screen --record-seconds 30 `
  --record-output screen_record.mp4 `
  -o reconstructed_project
```

新视频开头带 `QVP1` 参数二维码，解码端默认会扫描前 `12` 秒并自动匹配 4QR/30FPS/repeat/FEC/fast-screen 等参数；参数二维码里放得下文件清单时，也会自动补齐 FEC 恢复所需的分片大小。

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

如果加码端使用 `--fast-fec-4qr`，解码端使用 4QR/30FPS/FEC 预设：

```powershell
py -3 scripts\win_decoder_hd.py screen_record.mp4 -o reconstructed_project
```

旧视频没有 `QVP1` 参数头时，再手动指定预设：

```powershell
py -3 scripts\win_decoder_hd.py screen_record.mp4 --screen-fast-fec-4qr `
  --manifest-json hd_secure_stream.manifest.json `
  -o reconstructed_project
```

FEC 恢复需要知道缺失分片的原始大小。新视频会尽量从参数二维码内置文件清单恢复；如果日志提示参数二维码未包含文件清单，或要解旧视频，继续带上 `--manifest-json hd_secure_stream.manifest.json`。

并发和抗噪参数：

- `--workers 0`：默认自动选择最多 6 个 worker 并发识别视频帧。
- `--memory-gb 6`：默认 6GB 内存预算，用于限制同时排队的解码帧数量。
- `--noise-robust`：默认开启，快速 zxing 未扫全时会追加对比度、锐化、阈值、形态学等抗噪变体，适合有压缩噪声的录屏。
- `--no-auto-params`：关闭前置参数二维码自动匹配。

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
  npm-packages.tgz
```

## Electron 后台任务与日志

- 视频加码、专用播放、屏幕录制和视频解码由独立后台子进程执行，运行时可以切换页签、生成/解码文本二维码，也可以并行启动另一个视频任务。
- 每个视频任务按独立 `runId` 管理，某个任务结束不会误停止或解锁其他仍在运行的任务。
- GUI 按数据/FEC 分片实时显示进度。
- 加码完成后会自动把输出 MP4 填到专用播放器，减少误选录屏视频或测试视频的情况。
- 录屏完成后可自动填入解码页并开始解码；界面只保留完整视频录制与解码流程。
- 运行日志使用纯白文本，可鼠标框选复制，也可点击“复制日志”一次性复制全部内容；“打开日志目录”会打开落盘诊断日志。

## 录像建议

- 手机尽量正对屏幕，开始录制前长按画面锁定对焦和曝光。
- 距离屏幕 50cm 到 1m，必要时用 2x 或 3x 变焦，减少摩尔纹。
- 屏幕亮度建议 50% 到 70%，避免白底过曝。
- 如果缺片，优先增加发送端 `--passes` 和 `--repeat`，例如 `--passes 4 --repeat 5`。
- 解码端会尝试红框透视校正、降采样再放大、CLAHE 对比度增强、锐化、开运算和自适应二值化，以降低手机拍屏的摩尔纹影响。
- 解码端会优先尝试 `zxing-cpp`，再尝试 OpenCV QRCodeDetector，最后使用 pyzbar/zbar；哪个引擎能解就用哪个。
- 解码端默认开启连续帧多数投票：同一分片重复播放时，会把连续多帧拉平并二值化，按像素投票重建更干净的二维码。可用 `--vote-window 7 --vote-min-frames 4` 调大窗口，或 `--vote-window 1` 关闭。
- 解码端可用 `--confirm-copies 2` 要求同一分片至少两次解出相同 payload CRC 才入库；如果两次不一致，就继续等下一帧。仍然缺片时，增加发送端 `--passes` 或 `--repeat` 后重新生成同一个 4QR 视频。

## License

MIT License. See [LICENSE](LICENSE).
