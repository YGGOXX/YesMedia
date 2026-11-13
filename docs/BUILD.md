# 构建说明（可复现）

## 0. 环境

- Windows 10/11 x64，非管理员即可。
- Python 3.13（本机 `C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe`）。
- JDK 17（`G:\tools\jdk17`）、Android SDK 35（`G:\tools\android-sdk`）、Gradle 8.13（`G:\tools\gradle-8.13`）。
- 网络可访问 pypi 镜像、dl.google.com、maven.google.com、repo.maven.apache.org。

工程根：`E:\Documents\deepseek-harness\default-workspace\webgrab`。

## 1. 依赖（开发/测试用）

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-desktop.txt
```

## 2. 本地开发

```powershell
$env:PYTHONPATH="$PWD\core"
.venv\Scripts\python.exe -m webgrab.server --port 8765
```

Windows 开发需要 ffmpeg 在 PATH 或 `WEBGRAB_FFMPEG_DIR` 指向其目录（`third_party\ffmpeg\bin`）。
重新获取：把 gyan.dev essentials zip 放到 `third_party\`，运行 `tools\prepare_third_party.ps1`。
EXE 打包已停止，`desktop/` 仅保留开发启动器。

## 3. 构建 APK

一条命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_apk.ps1
```

或手动：

```powershell
$env:JAVA_HOME='G:\tools\jdk17'
$env:ANDROID_HOME='G:\tools\android-sdk'
$env:Path="G:\tools\jdk17\bin;$env:Path"

cd android
.\copy_python.ps1          # core/webgrab -> app/src/main/python/webgrab
.\gradlew.bat assembleRelease
```

产物：`android\app\build\outputs\apk\release\app-release.apk` → 复制为
`dist\网页抓取器-1.0.0-arm64.apk`，并写 `dist\SHA256SUMS.txt`。

签名：`android/keystore/webgrab.jks`（自签名，口令见 `android/gradle.properties`）。
只含 `arm64-v8a`；最低 Android 8.0（API 24），目标 API 35。

改过 `core/webgrab/**` 或 `core/webgrab/static/**` 后必须重跑 `copy_python.ps1`，否则 APK 还是旧代码/旧页面。

## 4. 测试

```powershell
# 后端（需 ffmpeg 在 PATH 或 WEBGRAB_FFMPEG_DIR）
.venv\Scripts\python.exe tools\smoke_test.py
.venv\Scripts\python.exe -m pytest tests -q

# 前端（需先启动服务；用系统 Edge，不下载 Chromium）
.venv\Scripts\python.exe tools\ui_test.py --base http://127.0.0.1:8765

# 抖音源探测（可选；Android 桥需真机）
.venv\Scripts\python.exe tools\douyin_probe.py "https://v.douyin.com/xxxx/"

# Android 真机（可选）
adb install -r dist\网页抓取器-1.0.0-arm64.apk
adb shell am start -n com.webgrab.app/.MainActivity
```

## 5. 已知构建风险

- 网络抖动：大文件用断点续传脚本（`G:\tools\dl_loop.py`）；TLS 失败时改用 Python urllib。
- Chaquopy 首次构建会从 maven.google.com 拉 AAR 与 pip 轮子，慢但可重试；pip 走清华镜像。
- ffmpeg-kit-maintained 的 16KB page 对齐要求 AGP 8.5.1+，本工程用 8.9.2。
- `build_apk.ps1` 含中文，必须保存为 UTF-8 **带 BOM**；PowerShell 5.1 需 `-ExecutionPolicy Bypass`。
- 真机安装若报 `INSTALL_FAILED_USER_RESTRICTED`，在手机开发者选项里打开「USB 安装」并允许弹窗。
