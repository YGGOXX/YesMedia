# 网页抓取器（WebGrab）

> 作者：YGGOXX · 仓库：YesMedia

本地网页媒体抓取工具：粘贴链接 → 解析图片/视频 → 预览、批量下载、视频转音频。
Python 服务只监听 `127.0.0.1`，前端是移动端优先的离线单页应用。

**当前交付：Android APK。** Windows 版源码仍可用于开发/调试，但不再提供 EXE。

## 交付物

- `dist/网页抓取器-1.0.0-arm64.apk` —— Android 8+（arm64），内置 Python、ffmpeg-kit 与前端。
- `dist/SHA256SUMS.txt`、`dist/使用说明.md`。

## 源码结构

```
core/webgrab/        平台无关 Python 核心 + 前端静态资源
android/             Gradle + Chaquopy + ffmpeg-kit 壳（WebView / 前台服务 / 下载 / 分享）
desktop/             Windows 开发启动器（仅调试用，不打包交付）
tools/               资源生成、冒烟测试、UI 测试、抖音探测
docs/                CONTRACT / HANDOFF / DOUYIN / BUILD
```

接口契约见 `docs/CONTRACT.md`。

## 本地开发（Windows，无需打包）

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
$env:PYTHONPATH="$PWD\core"
.venv\Scripts\python.exe -m webgrab.server --port 8765
# 浏览器打开 http://127.0.0.1:8765/
```

测试：

```powershell
.venv\Scripts\python.exe tools\smoke_test.py
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe tools\ui_test.py --base http://127.0.0.1:8765
```

## 已验证（2026-11-14）

- 后端：`tools\smoke_test.py` 74/74、`pytest tests` 26 passed。
- 前端：`tools\ui_test.py` 50 项全 PASS（预览、批量下载、转音频、`/share`、360/393/430 无横溢出）。
- APK：`aapt2` 包名/权限/ABI 正确，`apksigner` 验签 OK；`DouyinResolver` 与最新静态已确认打进 APK；Chaquopy 运行时、WebView、下载落盘、分享入口待真机复测。
- 抖音：`com.webgrab.app.douyin.DouyinResolver` 用隐藏 WebView 取真实 MP4 直链；桌面 UA 无水印优先、移动站 playwm 兜底。PC 已验证同款页面 4 秒内出直链且无 Cookie 可下载；Android 运行时待真机验证。详见 `docs/DOUYIN.md`。

## 构建 APK

需要 JDK 17、Android SDK 35、Gradle 8.13（本机路径见 `build_apk.ps1`）。
`build_apk.ps1` 会复制 Python 源与静态资源、跑 Gradle、把 APK 放进 `dist/` 并更新 SHA256。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_apk.ps1
```

细节见 `android/README.md` / `docs/BUILD.md`。

## 版权与第三方

- Chaquopy（MIT）、ffmpeg-kit-maintained（LGPL）：见 `third_party/THIRD-PARTY-NOTICES.md`。
- Windows 开发调试可选用独立 ffmpeg（GPLv3），见 `third_party/LICENSE-ffmpeg.txt`；不随 APK 交付。
- 本工具仅供个人学习与备份自己有权保存的内容，请遵守目标网站条款。
