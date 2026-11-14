# 第三方组件与许可（YesMedia 1.0.1）

本软件打包/调用了以下第三方组件。分发时请保留本文件与相应许可文本。

## Windows 版

| 组件 | 版本 | 许可 | 用法 |
|---|---|---|---|
| Python | 3.13 | PSF License | 运行时（PyInstaller 打包） |
| PyInstaller | 6.x | GPLv2 + 例外条款 | 仅构建工具 |
| requests | 2.34 | Apache-2.0 | HTTP |
| beautifulsoup4 | 4.15 | MIT | HTML 解析 |
| yt-dlp | 2026.10.26 | Unlicense | 平台解析 |
| pystray / Pillow | 0.19 / 13.0 | LGPLv3 / HPND | 托盘图标 |
| **FFmpeg** | 8.x essentials (gyan.dev) | **GPLv3** | 独立进程调用 `ffmpeg.exe`，用于 HLS 转封装、合并、转音频 |

FFmpeg 以独立可执行文件形式随包分发，通过命令行进程调用，未与主程序静态链接。
完整许可见 `third_party/LICENSE-ffmpeg.txt`；源码见 https://ffmpeg.org/download.html 。

## Android 版

| 组件 | 版本 | 许可 | 用法 |
|---|---|---|---|
| Chaquopy | 17.2.0 | MIT（Copyright 2017-2025 Chaquo Ltd） | 在 APK 中嵌入 Python 3.13 |
| ffmpeg-kit-maintained | 8.1.9 (`ffmpeg-kit-full`, LGPL 变体) | LGPL-3.0 | 进程内调用 FFmpegKit，用于 HLS/合并/转音频 |
| requests / beautifulsoup4 / yt-dlp | 同上 | 同上 | 同上 |
| AndroidX / Material | 按 AGP 依赖 | Apache-2.0 | UI 壳 |

ffmpeg-kit 使用 LGPL 变体（未启用 GPL 组件），动态链接；源码与构建说明：
https://github.com/arthenica/ffmpeg-kit （维护分支：`dev.ffmpegkit-maintained`）。

## 免责

本工具仅供个人学习、备份自己有权保存的内容。使用者需自行遵守目标网站服务条款与当地版权法律。
