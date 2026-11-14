# 抖音解析说明

抖音网页版自身不带免登录媒体地址：分享页 `_ROUTER_DATA` 里只有作品 id，播放地址由客户端签名接口（`a_bogus` + `uifid`）下发。直接请求页面或公开接口在 2026-11 已基本不可用。

## Android：隐藏 WebView（主路径）

App 在解析抖音时启动一个 1×1 的隐藏 WebView，加载真实视频页，等播放器拿到 MP4 后拦截/读取地址：

1. **第一轮（无水印优先）**：桌面 Chrome UA 打开 `https://www.douyin.com/video/<id>`，拦截 `douyinvod.com` / `mime_type=video_mp4` 请求，同时轮询 `<video>` 的 `currentSrc`。命中即返回，`watermark=false`，下载时带 `Referer: https://www.douyin.com/`。PC 无头 Edge 实测 4 秒左右出地址，无 Cookie 也能 200/Range。
2. **第二轮（兜底）**：8 秒没命中就销毁 WebView，换移动 UA 打开 `m.douyin.com/share/video/<id>`，读 `video.src` 的 `playwm` 地址。这条带水印，`watermark=true`，带 `Referer: https://m.douyin.com/`。
3. 总预算 25 秒；超时、加载失败、HTTP ≥400 都返回中文原因，不会卡住界面。

实现：`android/app/src/main/java/com/webgrab/app/douyin/DouyinResolver.kt`；Python 侧经 Chaquopy 调 `DouyinResolver.resolve(pageUrl, 25000)`，入口在 `core/webgrab/engines.py` 的 `_android_bridge_resolve`。

解析出的直链会过期。下载接口 `/api/dl` 收到 `via=dy` + `page=页面地址` 时，遇到 403/404/HTML 会自动重解析一次再重试；仍失败提示「抖音直链已过期，请重新解析」。

风险：抖音有登录墙、验证码、地区限制时两轮都可能失败；风控策略变化会影响成功率。此前已完成 Android 真机测试；不同设备、系统版本和网络环境下的表现仍需分别验证。

## 其它平台：三层链（兜底）

1. **直连分享页**：请求 `v.douyin.com/...` 或 `iesdouyin.com/share/video/...`，在 `_ROUTER_DATA`/`RENDER_DATA` 里递归找 `play_addr.url_list`、`video_url`、图集 `images[].url_list`。
2. **yt-dlp**：交给 yt-dlp 的抖音 extractor（多数视频要求登录，失败是常态）。
3. **第三方接口链**：按配置顺序 GET，把 `{url}` 替换成编码后的分享链接。返回 JSON 里只要能递归找到视频直链或图集地址就能用。

2026-11-10 起 `api.xingzhige.com`、`api.kxzjoker.cn`、`api.pearktrue.cn`、`api.oioweb.cn`、`api.52vmy.cn`、`api.qqsuu.cn`、`douyin.wtf`、`tikwm` 等公开接口先后超时或下线，默认接口很可能失效。

## 在 App 里换源

- 右上角 `?` →「抖音接口设置」，一行一个接口，最多 5 条。
- 每行必须是 `http(s)://...{url}...`；保存后立即生效。解析成功缓存 10 分钟，失败缓存 2 分钟。
- 关掉开关后不再请求第三方接口，只走 WebView/直连/yt-dlp。

开发时也可用环境变量（只影响进程）：

```powershell
$env:DOUYIN_API = "https://your-api.example/douyin?url={url}"
```

配置保存在 Android 私有目录 `files/webgrab/config.json`。

## 自建解析服务

抖音开源解析项目通常需要自己部署（Docker 或 Python），例如 `Douyin_TikTok_Download_API`。部署后确认提供类似：

```
GET https://your-host/api/hybrid/video_data?url=<分享链接>
```

然后把接口填成 `https://your-host/api/hybrid/video_data?url={url}`。只要返回体里带 `play_addr`/`video_url`/`.mp4` 或图集地址，本工具会自动归一化。自建服务需要自己维护 Cookie/签名。

## 已知限制

- 只支持公开可访问的作品；私密、好友可见、付费、DRM 内容抓不到。
- 图集（图文）支持；直播、合集不保证。
- 第三方接口会把分享链接发给对方服务器；不想外发就关掉开关。
- 请遵守目标网站条款与版权，仅下载你有权保存的内容。
