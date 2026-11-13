# WebGrab — 平台无关核心契约（v1，已冻结）

本文件是 `backend-core` / `frontend-ui` / `android-shell` 三方共同遵守的接口契约。
改动此文件必须由 Lead 同意；实现不得擅自改字段名。

## 1. 目录与职责

```
webgrab/
  core/webgrab/            # 纯 Python，桌面与 Android 共用
    __init__.py            # __version__ = "1.0.0"
    server.py              # HTTP 服务、路由、静态文件、SSE
    net.py                 # 会话、抓取、探测、文件名/大小工具
    extract.py             # HTML 媒体提取 + scan_page 编排
    engines.py             # 抖音三层链、yt-dlp 解析、平台判断
    hls.py                 # 用 ffmpeg 下载/转封装 HLS
    media.py               # ffmpeg/ffprobe 抽象（桌面 subprocess / Android FFmpegKit）
    convert.py             # 转音频（上传/链接/产物）
    config.py              # 数据目录与 config.json
    android_entry.py       # Android 入口（start(files_dir, cache_dir) -> port）
    static/                # 前端（frontend-ui 负责）
  desktop/                 # Windows 启动器 + PyInstaller spec（Lead）
  android/                 # Gradle 工程（android-shell）
  tools/                   # smoke_test.py、douyin_probe.py、ui_test.py、make_assets.py
  docs/                    # CONTRACT、HANDOFF、DOUYIN、BUILD
```

## 2. 运行环境

- Python 3.13（桌面）、Chaquopy 3.13（Android）。
- 依赖仅：`requests`、`beautifulsoup4`、`yt-dlp`。禁止 lxml、pycryptodome。
- **Android 上禁止 `subprocess` / `fork` / `os.system`**。所有 ffmpeg 调用必须走 `media.py`。
- 数据目录：`config.data_dir()`：
  - 环境变量 `WEBGRAB_HOME` 优先；
  - 否则 `Path.home()/".webgrab"`；
  - Android 入口设置 `WEBGRAB_HOME=<filesDir>/webgrab`。
- 只监听 `127.0.0.1`。端口从 8765 起顺延到 8799。`server.serve(port, static_dir)` 返回实际端口。
- Host 头校验：仅接受 `127.0.0.1[:port]`、`localhost[:port]`，否则 403。

## 3. 启动入口

桌面：`desktop/launcher.py` 调 `webgrab.server.main(static_dir)`；打包后 static 在 `sys._MEIPASS/webgrab/static`。

Android：Kotlin → `webgrab.android_entry.start(files_dir: str, cache_dir: str, static_dir: str) -> int`（返回端口；内部起线程，不返回）。

`server.main()` 行为：解析 `--port`、`--no-browser`，绑定成功后打印 `WEBGRAB_URL=http://127.0.0.1:<port>`；静态目录默认取 `Path(__file__).parent/"static"`。

## 4. HTTP API（全部 JSON 响应；失败也返回 HTTP 200 + `{"ok":false,"error":"中文原因"}`）

### GET `/api/health`
```json
{"ok":true,"version":"1.0.0","ffmpeg":true,"convert":true,"platform":"windows|android","port":8765}
```

### GET `/api/config` / POST `/api/config`
```json
{"ok":true,"douyin_apis":["https://api.example.com/douyin?url={url}"]}
```
POST 校验：数组、1..5 条、每条 `http(s)://` 且含 `{url}`；不合法返回 ok:false 与中文原因。写入 `data_dir()/config.json`。

### POST `/api/scan`  body `{"url":"...","allow_third_party":false}`
成功：
```json
{"ok":true,"url":"原链","title":"页面/作品标题","engine":"direct|douyin|ytdlp","note":"抖音第三方解析",
 "items":[{"id":"i1","kind":"image|video","url":"...","thumb":"...","name":"文件名","ext":"jpg",
           "size":12345,"w":1080,"h":1440,"referer":"https://原页面","source":"img"}],
 "groups":{"image":[...],"video":[...]}}
```
- `id` 稳定且唯一（`i1..iN`）。
- `url` 是可直接下载/代理的绝对 URL；`thumb` 可为空。
- `referer` 用于下载/代理防盗链；无则空串。
- 解析不到任何媒体 → `{"ok":false,"error":"这个页面没有能下载的东西"}`。
- 抖音/平台失败 → 中文原因（如「抖音解析失败：接口超时」）。

### GET `/api/scan_stream`（也接受 POST，body 同上）
SSE：`Content-Type: text/event-stream`，每事件 `data: <json>\n\n`：
1. `{"stage":"open","msg":"打开页面"}`
2. `{"stage":"probe","msg":"找到 N 个候选","found":N}`
3. `{"stage":"engine","msg":"平台解析"}`（可省略）
4. 结束：`{"stage":"done","ok":true,"result":{...同 /api/scan 成功体...}}`
   或 `{"stage":"done","ok":false,"error":"中文原因"}`
- 支持 `?url=&tp=1` GET 形式（前端 EventSource 用 GET）。
- 客户端断开不抛异常；写失败即停。

### GET `/api/dl?u=<urlencoded>&ref=<urlencoded>&name=<urlencoded>`
- 流式下载，带 `Referer`/UA；`Content-Disposition: attachment; filename*=UTF-8''<quoted>`。
- 支持 `Range`（206 + Content-Range）；上游不支持 Range 时忽略。
- 上游失败：若尚未发头 → JSON 错误；已发头 → 断开。

### GET `/api/proxy?u=&ref=`
- 预览/缩略图代理：透传 `Content-Type`、`Content-Length`、`Content-Range`、`Accept-Ranges`、`Last-Modified`、`ETag`。
- 支持 Range（206）。不设 attachment。

### GET `/api/hls?u=&ref=&name=`
- 下载 m3u8（自动最高码率、AES-128），ffmpeg 转封装为 mp4，完成后流式返回。
- 失败 → JSON `{ok:false,error}`（若已开始输出则断开）。
- 产物放 `data_dir()/tmp/`，结束后删除。

### GET `/api/yt?u=&ref=&name=`
- yt-dlp 解析 → 选最佳视频+音频 → 分别下载到临时文件 → `media.merge()` → 返回 mp4。
- 单文件格式（已含音轨）直接下载。
- 失败中文原因。

### 静态与页面
- `GET /` → `static/index.html`。
- `GET /demo` → `static/demo.html`。
- `GET /share?url=&text=&title=` → `static/index.html`（前端读 query 自动解析）。
- `GET /manifest.webmanifest`、`GET /sw.js`、`GET /static/*`、`GET /icons/*`、`GET /demo/*`。
- 所有静态文件必须支持 **Range**（206/Content-Range），尤其视频；带 `Cache-Control: no-cache` 给 html/js/css，媒体可 `max-age=3600`。
- 路径穿越防护：规范化后必须仍在 static 根内。

### 转音频
`POST /api/convert/upload?fmt=mp3|m4a|wav`
- body 裸字节；头 `X-Filename`（URL 编码）可选；上限 800MB（超限 413 JSON）。
- 成功：`{"ok":true,"id":"c1","name":"a.mp3","size":123,"url":"/api/convert/file?id=c1"}`

`POST /api/convert/link`  body：
```json
{"u":"...","ref":"...","via":"direct|hls|yt","fmt":"mp3","name":"原文件名"}
```
- 先按 via 取源到临时文件，再转；成功同上。

`GET /api/convert/file?id=c1`
- 返回产物，attachment；产物 TTL 1 小时，后台清理线程每 10 分钟清一次。

ffmpeg 参数：mp3 `-vn -c:a libmp3lame -b:a 192k`；m4a `-vn -c:a aac -b:a 128k`；wav `-vn -ar 44100 -ac 2 -c:a pcm_s16le`。转换前 `media.has_audio(src)`，无音轨 → `{"ok":false,"error":"这个文件里没有音轨"}`。

## 5. 模块 API（被 server 调用）

```python
# net.py
def session() -> requests.Session            # 线程本地，每线程一个
def http_get(url, *, referer="", timeout=20, allow_insecure=True) -> tuple[int, bytes, str]  # (status, body, final_url)
def probe(url, *, referer="", timeout=6) -> dict   # {"size":int|0,"ctype":str,"w":int,"h":int}
def filename_from(url, ctype="", fallback="download") -> tuple[str, str]  # (stem, ext)
def human_size(n) -> str

# extract.py
def scan_page(url, allow_third_party=False, progress=None) -> dict   # 成功体（不含 ok? 含 ok=True）
def extract_media(html: str, base_url: str) -> list[dict]           # 候选 {url,thumb,kind,source}

# engines.py
DEFAULT_DOUYIN_APIS: list[str]
def is_douyin(url) -> bool
def is_platform(url) -> str          # "douyin"|"bilibili"|"weibo"|"youtube"|""
def resolve_douyin(url, cfg, progress=None) -> dict|None   # {title, items:[...], engine:"douyin"}; 失败抛 EngineError
def ytdlp_resolve(url, progress=None) -> dict               # {title, items:[...], engine:"ytdlp"}
def ytdlp_download(url, out_dir, progress=None) -> tuple[str, str]  # (path, filename)

# hls.py
def download_hls(url, referer, out_path, progress=None) -> None   # 失败抛异常

# media.py
def ffmpeg_available() -> bool
def has_audio(path) -> bool
def convert_audio(src, fmt, out) -> None
def hls_to_mp4(url, referer, out) -> None
def merge(video, audio, out) -> None
def ffmpeg_version() -> str
```
`progress` 回调签名 `progress(stage: str, msg: str)`；实现可忽略。

## 6. 扫描流水线（extract.scan_page）

1. `http_get(url)`，UA 用 iPhone Safari；失败再试桌面 UA；SSL 证书错误降级 `verify=False`。
2. `extract_media(html, final_url)`：img/srcset/data-src/data-original、picture/source、video/src/poster、source、`a[href]` 指向图片/视频、og:image/twitter:image、内联 style 背景图、脚本里的 `https://...m3u8|mp4`。
3. 过滤：去重（按 URL）、排除 data:/blob:/javascript:、排除明显头像/图标/1px（`favicon`、`logo`、`avatar`、`icon`、`sprite`、`blank`）、同域外链仅当 allow_third_party 或图片。
4. `build_items`：最多 120 个候选，10 线程 `probe` 补 size/ctype/宽高（从 URL 无宽高则 0）；单请求超时 6s，同域连续超时 3 次则跳过剩余探测。
5. 若 items 为空：平台判断 → 抖音三层链 / yt-dlp；仍空 → 失败。
6. 排序：视频在前、图片在后，各自保持原顺序。

## 7. 抖音三层链（engines.resolve_douyin）

目标：短链/分享文案/视频 ID 均可。
1. **直连解析**：GET 分享页（`https://www.iesdouyin.com/share/video/<id>/`，短链先跟随 302 取 ID），从 `_ROUTER_DATA` / `RENDER_DATA` / `__INITIAL_STATE__` 递归归一化：
   - 视频：`play_addr.url_list[]`、`video.play_addr`、`video_url`、`download_addr.url_list`、任意以 `.mp4`/`.m3u8` 结尾的 URL；
   - 图集：`images[].url_list[]`（取每个最后一项）；
   - 标题：`desc`/`title`/`item_title`。
2. **yt-dlp**：`ytdlp_resolve`（抖音 extractor 可能已失效，失败就跳过）。
3. **第三方 API 链**：`config.douyin_apis` 逐条 GET（`{url}` 替换为 URL 编码后的原始输入），8s 超时；响应 JSON 用**通用归一化**（不依赖固定结构）：递归找 `play_addr.url_list`、`video_url`、`url`（值以 .mp4/.m3u8 结尾）、`images[].url_list`；标题取 `desc/title`。任一成功即返回。
- 缓存：成功 10 分钟，失败 2 分钟（key=原始 URL）。
- 错误文案带尝试过的层：「抖音解析失败：直连无数据 / 第三方接口超时」。

## 8. 前端契约（frontend-ui）

- 页面骨架以 `core/webgrab/static/index.html` 为准（170 行，勿重写结构）。
- 新增：`guide` 卡底部小字按钮 `#dyApiBtn`「抖音接口设置」，点击 `prompt` 输入一个 URL 模板 → POST `/api/config`；当前值 GET 预填。
- 解析：优先 `EventSource('/api/scan_stream?url=...&tp=...')`；`onerror` 或 8s 无事件 → 回退 `POST /api/scan`。
- 下载：`/api/dl?u=&ref=&name=`；HLS 用 `/api/hls`；yt 用 `/api/yt`；预览用 `/api/proxy?u=&ref=`。
- 批量：队列 900ms 间隔，逐个 `<a download>` 点击。
- 转音频：上传用 fetch POST body，头 `X-Filename`；链接用 `/api/convert/link`；完成后 `location.href` 产物 URL。
- 错误/进度文案全中文；任何 >300ms 操作必须有可见状态（保留现有 progress/viewer/toast）。
- Android 下隐藏「放到桌面」（`navigator.userAgent` 含 `WebGrabAndroid`，由 WebView 设置）。
- 图标：192/512/maskable PNG；demo：4 图 + 6s mp4 + 封面（由 make_assets.py 生成）。

## 9. Android 壳契约（android-shell）

- applicationId `com.webgrab.app`，versionName 1.0.0，versionCode 1，minSdk 24，targetSdk 35，arm64-v8a。
- Chaquopy 3.13 + pip `requests`、`beautifulsoup4`、`yt-dlp`；ffmpeg-kit `dev.ffmpegkit-maintained:ffmpeg-kit-full:8.1.9`（LGPL）。
- Python 源目录 `android/app/src/main/python`，构建前由 `android/copy_python.ps1` 从 `core/webgrab` 复制（不要手改副本）。
- 前台服务 `GrabService`（`foregroundServiceType="dataSync"`）启动 Python；失败通知中文原因。
- `MainActivity`：WebView（JS、DOM storage、file chooser、下载监听、UA 追加 `WebGrabAndroid`），启动后加载 `http://127.0.0.1:<port>/`。
- 下载：`DownloadListener` → `DownloadManager` 到 `Downloads/网页抓取器/`，通知栏可见；Android 10+ 用 `setDestinationInExternalPublicDir` + `IS_PENDING`。
- 分享：`ACTION_SEND` text/plain → 启动后加载 `/share?text=...&url=...`。
- 权限：INTERNET、POST_NOTIFICATIONS（33+ 运行时申请）、FOREGROUND_SERVICE、FOREGROUND_SERVICE_DATA_SYNC、WAKE_LOCK；`usesCleartextTraffic=true`。
- 退出：Activity 关闭不杀服务；通知栏「停止」按钮停服务。
- WebView 调试：debug 构建 `WebView.setWebContentsDebuggingEnabled(true)`。

## 10. 错误与安全

- 所有面向用户错误为中文、单句、可行动。
- 不记录用户 URL 到日志文件（桌面日志只记启动/错误）。
- 只绑 127.0.0.1；校验 Host；静态路径不得逃逸。
- 下载文件名清洗 `[\\/:*?"<>|\r\n]` → `_`，长度 ≤80。
- 转音频产物 1h 清理；HLS/yt 临时文件结束即删。

## 11. 验收测试（tools/）

- `smoke_test.py`：启动服务于随机端口 → health → `/demo` 扫描 5 项 → `/api/proxy` Range 206 → `/api/dl` 下载 1 个 demoS → 上传 demo.mp4 转 mp3/m4a/wav → `/share`。
- `douyin_probe.py <url>`：逐层打印直连/yt-dlp/各 API 结果（不依赖 App）。
- `ui_test.py`：Playwright + 系统 Edge（`channel="msedge"`），393×852，覆盖预览、切换、双击缩放、Esc、选中、全选、批量下载、上传转音频、`/share`。
- 无设备时 Android 只做 `assembleRelease` + `aapt2 dump badging` 验证；有 adb 设备时安装并跑 WebView CDP 冒烟。
