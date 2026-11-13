# 网页抓取器 · 项目交接文档

> 给接手的人 / AI：这份文档是自包含的，看完就能改。
> 写于 2026-11-14，服务当时正在 `127.0.0.1:8765` 运行。

---

## 1. 项目一句话

小米手机上的**本地网页抓取工具**：在本机跑一个 Python 服务，手机浏览器打开 `http://127.0.0.1:8765`，粘贴链接或分享文案 → 解析出图片/视频 → 下载；另带大图预览、批量全选下载、视频转音频（MP3/M4A/WAV）。

- 只监听 `127.0.0.1`，不对外，不上传内容。
- 唯一外部依赖：抖音解析接口（当前不可用，见第 9 节）。
- 用户环境：小米手机 + **Edge**（默认浏览器）+ 小米浏览器；系统未 root；DSH App 里的 Ubuntu 24.04 proot。

**用户的诉求演变**（重要背景）：
1. 抓网页图片/视频下载 → 2. 抖音分享链接识别 → 3. 体验太差（没进度、没动画）→ 4. 点开大图 + 批量全选下载 + 视频转音频 → 5. 一键打开/分享（桌面图标）。

---

## 2. 快速开始

```bash
cd /root/workspace/webgrab
./start.sh                 # 启动；已在跑会提示
python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8765/api/health').read())"
```

- 日志：`/tmp/webgrab.log`
- 停止：`pkill -f "server.py"`（注意别把自己命令行也匹配掉），或找 PID：`pgrep -af "[s]erver.py"`
- 端口：默认 8765，`./start.sh 9000` 可换。
- **没有自启动**：DSH/引擎重启后服务会停，需要重新 `./start.sh`（用户会说「启动抓取器」）。

依赖（已装）：`requests`、`beautifulsoup4`、`lxml`、`pycryptodome`、`yt-dlp`、`ffmpeg`（含 libmp3lame/aac）、`playwright + chromium`（仅测试用）。

---

## 3. 目录结构

```
/root/workspace/webgrab/
├── server.py            1330 行，HTTP 服务全部逻辑
├── engines.py            260 行，抖音接口 + yt-dlp 封装 + 分享文案取链接
├── start.sh              启动脚本（端口探测 + setsid nohup）
├── README.md             用户向说明
├── static/
│   ├── index.html        170 行，结构（含预览层、转音频卡片）
│   ├── app.js            724 行，前端逻辑
│   ├── style.css         423 行，深色主题/动画
│   ├── sw.js             Service Worker（PWA、分享目标需要）
│   ├── manifest.webmanifest  PWA 清单，含 share_target
│   ├── demo.html         内置示例页
│   ├── demo/             示例素材（4 图 + 6s mp4 + 封面）
│   └── icons/            PWA 图标 192/512/maskable
├── downloads/tmp/        转音频产物（TTL 1 小时）
└── shots/                界面截图
```

---

## 4. 架构与数据流

### 4.1 扫描

```
POST /api/scan_stream (SSE) 或 POST /api/scan
  └─ scan_page(url, allow_third_party, progress)
       ├─ http_get 页面（带 UA/Referer，SSL 失败降级 verify=False）
       ├─ extract_media()  ← bs4
       │    img / srcset / data-src / poster / 背景图 / og:image / video/source / a[href] / 脚本里的 m3u8|mp4
       ├─ build_items()    ← 10 线程探测 size/content-type（同域超时短路）
       └─ 平台兜底
            ├─ 抖音：engines.resolve_douyin()（第三方 API，缓存 10 分钟）
            └─ 其他/空：engines.ytdlp_item()（B站、微博、YouTube…）
```

SSE 阶段：`open`（打开）→ `probe`（分析）→ `engine`（平台解析）→ `done`。
前端进度卡显示阶段 + 耗时；`EventSource` 失败自动回退 POST。

### 4.2 下载

| 类型 | 接口 | 说明 |
|---|---|---|
| 直链/防盗链 | `/api/dl` | 带原页面 Referer，流式 |
| HLS | `/api/hls` | 选最高码率、AES-128 解密、ffmpeg 管道转 fMP4 |
| B站等 | `/api/yt` | yt-dlp 下载 + ffmpeg 合并 mp4 |
| 转音频 | `/api/convert/*` | 见 4.3 |

### 4.3 视频转音频

- 上传：`POST /api/convert/upload?fmt=mp3|m4a|wav`，body 为裸字节，头 `X-Filename`（URL 编码），上限 800MB。
- 链接：`POST /api/convert/link`，`{u, ref, via:"direct|hls|yt", fmt, name}`，服务端抓取后转。
- 下发：`GET /api/convert/file?id=`，产物保留 1 小时。
- 转码：`ffprobe` 校验（必须有音轨）→ `ffmpeg -vn`（mp3 192k / m4a 128k / wav 44.1k 16bit）。
- 失败一律 `{ok:false, error:"中文原因"}`。

---

## 5. HTTP 接口表

| 方法 | 路径 | 参数 / 说明 |
|---|---|---|
| GET | `/` `/demo` `/share` | 主页 / 示例 / 分享入口（`?url=&text=&title=` 自动解析） |
| GET | `/api/health` | `{ok, ffmpeg, convert}` |
| GET | `/api/scan_stream` | `?url=&third_party=1`，SSE |
| POST | `/api/scan` | `{url, allow_third_party}` |
| GET | `/api/proxy` | `?u=&ref=`，媒体代理，透传 Range |
| GET | `/api/dl` | `?u=&ref=&name=` |
| GET | `/api/hls` | `?u=&ref=&name=` |
| GET | `/api/yt` | `?u=&ref=&name=` |
| POST | `/api/convert/upload` | `?fmt=`，`X-Filename` |
| POST | `/api/convert/link` | JSON `{u,ref,via,fmt,name}` |
| GET | `/api/convert/file` | `?id=` |
| GET | `/manifest.webmanifest` `/sw.js` | PWA |

---

## 6. 前端要点（别改坏）

- 交互：**点缩略图=预览**，**左上角圆圈=选中**，**右下角 ⤓=单独下载**。
- 预览层 `#viewer`：左右滑/‹›切换、双击缩放、Esc 关闭、预览内可选中/下载/转音频。
- 批量：底部栏「全选/全不选 + 已选 N/共 M + 下载」；结果页「全部下载」下全部；队列间隔 900ms 防止浏览器拦截。
- 转音频卡片：折叠、格式三选一、上传进度、完成自动下载；预览里的「转音频」走 `/api/convert/link`。
- PWA：`manifest.webmanifest` + `sw.js` + 顶栏「放到桌面」；`/share` 带参自动解析。

---

## 7. 已修复的坑（回退会出问题）

1. **静态文件必须支持 Range**：否则预览视频播不了；`_file()` 已实现 206/Content-Range，代理透传。
2. **`requests.Session` 每线程一个**（`_session()`）：共享会话在并发分段时会把视频数据串坏。
3. `.mini` 类名曾与胶囊按钮冲突导致 fixed 错位，已改名 `.hud`。
4. 抖音接口认分享短链，不认跳转后的长参数链接；`scan_page` 用 `raw` 原链。
5. 分享参数解析后 `history.replaceState('/')`，避免刷新重复解析。
6. 首次打开自动跑示例；带分享参数时不跑。
7. 全选只作用于当前分类；「全部下载」下载全部。

---

## 8. 测试方法

### 8.1 接口冒烟

```bash
# 健康
python3 -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8765/api/health').read().decode())"

# 示例扫描（应出 5 项）
python3 - <<'PY'
import json,urllib.request
r=urllib.request.Request('http://127.0.0.1:8765/api/scan',
  data=json.dumps({'url':'http://127.0.0.1:8765/demo'}).encode(),
  headers={'Content-Type':'application/json'})
d=json.loads(urllib.request.urlopen(r).read())
print(d['engine'], len(d['items']), [i['kind'] for i in d['items']])
PY

# 上传转 mp3
python3 - <<'PY'
import requests
with open('static/demo/demo.mp4','rb') as f:
    r=requests.post('http://127.0.0.1:8765/api/convert/upload?fmt=mp3', data=f,
                    headers={'X-Filename':'demo.mp4'}, timeout=120)
print(r.status_code, r.text[:120])
PY
```

### 8.2 无头浏览器（已装 Playwright + Chromium）

要点：`chromium.launch(args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])`，视口 393×852。
可测：点卡片出 `#viewer`、`#vwNext` 切换、双击 `img.zoom`、Esc 关闭、点 `.check` 选中、`#selAll` 计数、`#dlAll` 触发 5 次下载（hook `HTMLAnchorElement.prototype.click`）、`set_input_files('#videoFile')` 转码、`/share?url=` 自动解析。

### 8.3 真机

- 浏览器打开 `http://127.0.0.1:8765`，示例 → 点图预览 → 全选下载 → 上传视频转 mp3，确认「文件管理→下载」出现文件。
- 抖音链接目前会失败（见第 9 节），不是回归。

---

## 9. 已知问题（按优先级）

### P0 抖音解析不可用
- `engines.DOUYIN_API` 默认 `https://api.xingzhige.com/API/douyin/`，自 2026-11-10 起 **连接超时**（此前返回「源异常」）。
- 已试：kxzjoker、pearktrue、52vmy、sumt、douyin.wtf、tikwm、oioweb、qqsuu、vvhan —— 均不可用/被墙/404。
- 抖音官网、百度正常，排除本机网络；抖音 Web API 需要浏览器签名（a_bogus + uifid），纯请求拿不到。
- 可用环境变量换源：`DOUYIN_API="https://your/api?url=" ./start.sh`。
- 该接口返回结构约定：`{"code":0,"data":{"item":{"url":"<mp4>","title":"…","cover":"…","size":123,"height":720,"images":[…]}}}`。

### P1 桌面一键图标没做成
- Edge 的 PWA「安装应用」会跳到 **应用信息** 页（MIUI 上 WebAPK 安装失败）。
- 小米浏览器菜单里「添加到桌面」是**灰色不可用**。
- 现有可用工具：`com.x7890.shortcutcreator`（创建快捷方式），已打开到「新建快捷方式」表单。
  - 表单字段：`快捷方式的名称`、`包名`、`活动`、`附加Data(Uri)`。
  - 建 URL 快捷方式思路：包名 `com.android.browser`（或 `com.microsoft.emmx`），活动为对应浏览器 Activity，`附加Data` 填 `http://127.0.0.1:8765`。
  - 启动该 App 时系统会弹「DSH Go 想要打开…」，属安全页，DSH 不能代点，需要用户允许。
- 备选：`am start` 在 proot 里被 SecurityException 拦住（uid 不对），不能绕过。

### P2 服务不常驻
- 服务由 `setsid nohup` 挂在 DSH 引擎下，App/引擎重启即停，桌面图标和分享都会打不开。
- 建议：写一个 dsh-go 原生插件（`$DSH_HOME/dsh-go/plugins/`，`dsh plugin --profile web add`）加「启动抓取器」胶囊：先探测/启动服务，再返回提示或打开链接（插件 `link` 效果只接受 https，需确认 http 是否可用；否则用 toast + 用户点浏览器）。

### P3 分享目标需先安装
- manifest 已配 `share_target`（`/share`，GET，params url/text/title），但 PWA 安装没成功，系统分享面板里还看不到「抓取器」。

---

## 10. 接手建议（按顺序）

1. **修抖音源**（最高价值）：找可用接口，或实现本地签名（a_bogus/uifid，成本高），或让用户提供接口；在 `engines.resolve_douyin()` 做多源切换 + 超时降级。
2. **做桌面图标**：用「创建快捷方式」建 URL 快捷方式（需用户允许一次），并验证点开能到页面。
3. **服务常驻/一键启动**：dsh-go 插件胶囊或开机脚本。
4. **补齐 PWA**：若安装仍失败，考虑改 manifest（`display: standalone`、图标、`start_url`）或用 `com.android.browser` 的书签快捷方式。
5. 文档：`README.md` 与本文同步更新。

---

## 11. 用户偏好 / 沟通注意

- 中文交流，手机屏幕小：回复短、结论先行。
- 反感「介绍模糊、没有进度、页面丑」；任何耗时操作都要有可见反馈。
- 已经踩过「点开始解析没反应」的坑：保留进度条、阶段文案、耗时。
- 用户手机上有 Shizuku/Dhizuku、创建快捷方式、Edge、小米浏览器；无 Chrome。
- DSH 手机操控：系统安全弹窗、安装页、锁屏时不能操作；屏幕被标记「安全窗口」时读不到。

---

## 12. 关键代码位置

| 位置 | 内容 |
|---|---|
| `server.py` `scan_page()` | 扫描主流程、平台兜底 |
| `server.py` `extract_media()` / `build_items()` | 资源提取与探测 |
| `server.py` `_file()` | 静态文件 + Range |
| `server.py` `handle_proxy()` | 媒体代理（Range、线程会话） |
| `server.py` `handle_hls()` / `resolve_hls()` | HLS 下载解密、ffmpeg 转封装 |
| `server.py` `handle_convert_*()` / `extract_audio()` | 转音频 |
| `engines.py` `resolve_douyin()` | 抖音第三方接口（改这里换源） |
| `engines.py` `ytdlp_item()` / `ytdlp_download()` | yt-dlp |
| `static/app.js` `scan()` / `renderViewer()` / `downloadMany()` / `uploadAndConvert()` | 前端核心 |
| `static/manifest.webmanifest` | PWA / share_target |

---

## 13. 交接验收清单

- [ ] `./start.sh` 后 `/api/health` 返回 `ok:true`
- [ ] `/demo` 扫描出 5 项
- [ ] 点图能预览、圆圈能选中、全部下载能触发 N 次
- [ ] 上传视频能转 mp3/m4a/wav 并下载
- [ ] 抖音链接能解析（当前阻塞，修好后必测）
- [ ] 桌面图标能一键打开（当前未完成）
- [ ] 分享面板能选「抓取器」并自动解析（依赖安装）
