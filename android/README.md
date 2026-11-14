# YesMedia Android 应用模块

YesMedia 本地网页媒体解析与下载工具的原生外壳：`MainActivity` 里放一个 WebView，显示跑在 `127.0.0.1` 上的
WebGrab 前端；Python 后端（含 ffmpeg）跑在 `GrabService` 前台服务里，通过 Chaquopy 嵌入。

- 包名 `com.webgrab.app`，`minSdk 24` / `targetSdk 36` / `compileSdk 36`
- 目标 ABI 为 `arm64-v8a`、`x86_64`（需构建及运行验证）（ffmpeg-kit 8.1 也只发布 arm64-v8a、x86_64）
- Python 3.13（Chaquopy 17.2.0），依赖 `requests`、`beautifulsoup4`、`yt-dlp`
- ffmpeg：`dev.ffmpegkit-maintained:ffmpeg-kit-full:8.1.9`（LGPL 版，支持 SDK 35 与 16KB 页对齐）

## 目录

```
android/
  app/src/main/
    java/com/webgrab/app/     MainActivity / GrabService / WebGrabApp / Errors
    java/com/webgrab/app/douyin/  DouyinResolver（隐藏 WebView 解析直链）
    python/webgrab/           ← 由 core/webgrab 复制生成，勿手改
    assets/webgrab-static/    ← 由 core/webgrab/static 复制生成，勿手改
    res/                      图标、主题、字符串
  copy_python.ps1             构建前同步 Python 源码
  keystore/webgrab.jks        自签名 release 密钥库
```

## 环境要求

| 组件 | 版本 / 路径 |
| --- | --- |
| JDK | 17（`JAVA_HOME=G:\tools\jdk17`） |
| Android SDK | `ANDROID_HOME` 指向本机 SDK，需 `platforms;android-36`、对应 Build Tools、`platform-tools` |
| Gradle | 8.13（仅用于生成 wrapper；日常构建用 `gradlew.bat`） |
| 构建机 Python | `webgrab/.venv/Scripts/python.exe`，必须是 **Python 3.13**（与 Chaquopy 目标版本一致） |

一次性的 SDK 安装：

```powershell
$env:JAVA_HOME='G:\tools\jdk17'
& G:\tools\android-sdk\cmdline-tools\latest\bin\sdkmanager.bat "platforms;android-35" "build-tools;35.0.0" "platform-tools"
```

## 构建步骤

```powershell
$env:JAVA_HOME='G:\tools\jdk17'
$env:ANDROID_HOME='G:\tools\android-sdk'
$env:Path="G:\tools\jdk17\bin;$env:Path"

cd E:\Documents\deepseek-harness\default-workspace\webgrab\android
powershell -NoProfile -ExecutionPolicy Bypass -File .\copy_python.ps1   # 必须：同步 core/webgrab
.\gradlew.bat assembleDebug         # 产物 app\build\outputs\apk\debug\app-debug.apk
.\gradlew.bat assembleRelease       # 产物 app\build\outputs\apk\release\app-release.apk（已签名）
```

`copy_python.ps1` 兼容 Windows PowerShell 5.1 与 PowerShell 7（老系统默认执行策略禁止运行脚本，
所以上面用 `-ExecutionPolicy Bypass`）。

若 wrapper 卡在下载 Gradle 发行版，直接用本机发行版，不要等：

```powershell
& G:\tools\gradle-8.13\bin\gradle.bat --no-daemon :app:assembleDebug
```

> **本机网络注意**：本机 dl.google.com/pypi 走 fake-ip 透明代理，Java 默认 TLS 1.3 握手会被对端
> 掐断，`gradle.properties` 里已固定 `systemProp.jdk.tls.client.protocols=TLSv1.2`；Chaquopy 的 pip
> 直连 pypi.org 会假死，`app/build.gradle.kts` 的 `chaquopy.pip` 已加 `--index-url` 指向清华镜像
> （仅构建期生效，不进 APK）。若换网络仍卡住，先 `.\gradlew.bat --stop`，删 `app\build\python`
> 后重跑。


`app/src/main/python` 与 `app/src/main/assets/webgrab-static` 是生成物；改了 `core/webgrab`
之后重新跑一次 `copy_python.ps1` 再构建，不要直接改这两处副本。

产物自检：

```powershell
& "$env:ANDROID_HOME\build-tools\35.0.0\aapt2.exe" dump badging app\build\outputs\apk\debug\app-debug.apk
```

## 签名

`keystore/webgrab.jks` 是自签名密钥库，口令写在 `gradle.properties`：

```properties
WEBGRAB_STORE_FILE=keystore/webgrab.jks
WEBGRAB_STORE_PASSWORD=webgrab2026
WEBGRAB_KEY_ALIAS=webgrab
WEBGRAB_KEY_PASSWORD=webgrab2026
```

重新生成（口令按需改）：

```powershell
keytool -genkeypair -v -keystore android\keystore\webgrab.jks -storetype JKS `
  -alias webgrab -keyalg RSA -keysize 2048 -validity 10000 `
  -storepass webgrab2026 -keypass webgrab2026 `
  -dname "CN=WebGrab, OU=WebGrab, O=WebGrab, L=Local, ST=Local, C=CN"
```

> 这是本地自用签名，口令公开在仓库里；只用于自己安装，不用于分发。

## 运行时行为

- 启动：`MainActivity` 用 `startForegroundService` 拉起 `GrabService`，服务在后台线程
  `Python.start()` → 设 `WEBGRAB_HOME=<filesDir>/webgrab`、`WEBGRAB_STATIC_DIR=<filesDir>/static`
  → `webgrab.android_entry.start(port=8765, static_dir=...)`（返回实际端口），把端口写进
  `SharedPreferences("webgrab")`；Activity 轮询 `/api/health`，通了才 `loadUrl` 前端页面。
- 静态资源：`assets/webgrab-static` 在服务启动时解压到 `filesDir/static`，作为 `static_dir` 传下去。
- 停止：通知栏「停止」→ `stopForeground` + `stopSelf` + 结束进程（Python 线程没有可控关闭点，
  杀进程才能真正释放端口）。
- 分享：系统分享 `text/plain` 进来 → 加载 `/share?text=&url=`。
- 下载：WebView `DownloadListener` → `DownloadManager`，保存到公共目录 `下载/YesMedia/`；
  Android 10+ 的 IS_PENDING 由 DownloadManager 内部处理，Android 9 及以下会先申请
  `WRITE_EXTERNAL_STORAGE`。
- 通知：Android 13+ 首次启动会申请 `POST_NOTIFICATIONS`（拒绝也能用，只是看不到常驻通知）。
- 调试构建在 `WebGrabApp` 里打开 `WebView.setWebContentsDebuggingEnabled(true)`；release 不开。

## 抖音解析（隐藏 WebView）

第三方解析接口失效后，壳里保留了一条本地兜底：用 1x1 隐藏 WebView 打开真实播放页，抓
`<video>` 的直链，交给 Python 侧下载（`com.webgrab.app.douyin.DouyinResolver`）。

```kotlin
// 任意后台线程可调，绝不抛异常
DouyinResolver.resolve("https://www.douyin.com/video/7300000000000000000", 25_000)
```

Python / Chaquopy 侧：

```python
from java import jclass
raw = jclass("com.webgrab.app.douyin.DouyinResolver").resolve(url)  # 返回 str(JSON)
```

返回 JSON：成功
`{"ok":true,"video_url":"...","title":"...","cover":"...","error":"","referer":"https://www.douyin.com/","watermark":false}`；
失败 `{"ok":false,"error":"中文原因"}`。

两轮策略（总预算 25s）：

1. 桌面 Chrome UA 打开原视频页，注入 `video.play()`，`shouldInterceptRequest` 拦
   `douyinvod.com` / `mime_type=video_mp4` 的媒体请求，同时每 700ms 注入 JS 读
   `currentSrc`（封面取 `video.poster`，其次 `og:image`）。命中即返回，`watermark=false`，
   `referer` 用 `https://www.douyin.com/`。
2. 第一轮 8s 无结果 → 切移动 UA 打开 `https://m.douyin.com/share/video/<id>`，只读
   `video.src`（通常已是 `aweme/v1/playwm/?video_id=...`）。拿到也算成功，`watermark=true`、
   `referer=https://m.douyin.com/`（带水印，但能下载）。

实现约束：只建 WebView，不新增权限、不改 manifest；能拿到当前 Activity 就挂 1x1 视图
（否则游离加载）；结束后主线程 `destroy()`。

## 验证状态\n\n1.0.1 的构建与设备验证尚待执行；下方旧版 1.0.0 数据仅作为历史记录，不代表 1.0.1 测试结果。\n\n## 1.0.0 历史验证记录

| 项目 | 结果 |
| --- | --- |
| `:app:assembleDebug` | BUILD SUCCESSFUL，`app-debug.apk` 62,447,707 字节（含抖音解析器） |
| `:app:assembleRelease` | BUILD SUCCESSFUL，`dist/YesMedia-1.0.0-arm64.apk` 61,460,462 字节（已用 `webgrab.jks` 签名） |
| `aapt2 dump badging` | package `com.webgrab.app`、versionName `1.0.1`、minSdk `24`、targetSdk `36`、native-code `arm64-v8a`, `x86_64`；权限 INTERNET / ACCESS_NETWORK_STATE / POST_NOTIFICATIONS / FOREGROUND_SERVICE / FOREGROUND_SERVICE_DATA_SYNC / WAKE_LOCK / WRITE_EXTERNAL_STORAGE(maxSdk 28) |
| `apksigner verify` | OK，CN=WebGrab，SHA-256 `780344aa…5753ed` |
| `zipalign -c -P 16` | 全部 `lib/arm64-v8a/*.so` 16KB 对齐 OK（含 libpython3.13、libffmpegkit、libavcodec 等） |
| APK 内容 | `assets/chaquopy/app.imy` 内含 `webgrab/*.pyc`（10 个模块 + convert 子包 + static）；`assets/webgrab-static` 16 个静态文件；arm64 原生库齐全；`classes.dex` 含 `com.webgrab.app.douyin.DouyinResolver` 与桌面 UA / `mime_type=video_mp4` / `playwm` 常量 |

1.0.1 APK 的 SHA-256 必须在最终构建后重新生成；此处不沿用旧版本哈希。

## 已知限制

- 未在真机上验证（本机无 arm64 Android 设备）：WebView 渲染、下载落盘、通知按钮、Chaquopy 启动
  全链路只做了编译与 APK 静态校验。
- `blob:` / `data:` 下载链接无法交给 DownloadManager，会提示「暂不支持下载该链接」。
- 停止服务会结束整个进程（包括正在看的页面）。
- 抖音解析未在真机验证：桌面 UA 是否能稳定拿到无水印直链、m 站 playwm 兜底是否可用，都依赖抖音
  当前页面行为；风控/验证码/登录墙会让两轮都失败（返回中文原因，不影响其他功能）。解析需要真实
  WebView，单次耗时几百 ms 到 8s+，必须在后台线程调用。
- ffmpeg-kit 8.1 的 LGPL 版只带 arm64-v8a / x86_64；x86_64 被 `abiFilters` 排除，模拟器装不上。
