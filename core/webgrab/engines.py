"""平台解析：抖音三层链、yt-dlp 封装。"""

from __future__ import annotations

import copy
import json
import re
import sys
import threading
import time
from html import unescape
from urllib.parse import quote, unquote, urljoin, urlsplit

from . import config, net

DEFAULT_DOUYIN_APIS = list(config.DEFAULT_DOUYIN_APIS)

_VIDEO_ID_RE = re.compile(r"/(?:share/)?(?:video|note|slides)/(\d{6,})")
_PAGE_KIND_RE = re.compile(r"/(?:share/)?(video|note|slides)/(\d{6,})")
_BARE_ID_RE = re.compile(r"(?<!\d)(\d{15,25})(?!\d)")
_MEDIA_URL_RE = re.compile(r"\.(?:mp4|m3u8)(?:[?#]|$)", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_MEDIA_TEXT_RE = re.compile(
    r"""https?://[^\s"'\\<>]+?\.(?:mp4|m3u8)(?:\?[^\s"'\\<>]*)?(?![\w.])""",
    re.IGNORECASE,
)
_JSON_SCRIPT_RE = re.compile(
    r"<script[^>]*(?:id=[\"'](?:RENDER_DATA|_ROUTER_DATA|__INITIAL_STATE__)[\"']"
    r"|type=[\"']application/json[\"'])[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)

_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()
_TTL_OK = 10 * 60
_TTL_FAIL = 2 * 60

_DOUYIN_REFERER = "https://www.douyin.com/"
_DOUYIN_BRIDGE_CLASS = "com.webgrab.app.douyin.DouyinResolver"
_DOUYIN_TEXT_RE = re.compile(
    r"(?:https?://)?(?:v\.douyin\.com/[A-Za-z0-9_\-]+"
    r"|(?:www\.)?douyin\.com/(?:video|note|slides)/\d{6,})",
    re.IGNORECASE,
)
_BRIDGE_LOCAL = threading.local()


class EngineError(Exception):
    """平台解析失败，消息直接展示给用户（中文），code 用于前端区分动作。"""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = (code or "").strip()


# ------------------------------------------------------------------ 平台判断


def is_douyin(url: str) -> bool:
    text = (url or "").lower()
    return any(
        host in text
        for host in ("douyin.com", "iesdouyin.com", "v.douyin.com", "douyin.com/")
    )


def is_platform(url: str) -> str:
    host = net.host_of(net.first_url(url) or url)
    mapping = (
        ("douyin", ("douyin.com", "iesdouyin.com")),
        ("bilibili", ("bilibili.com", "b23.tv", "bili2233.cn")),
        ("weibo", ("weibo.com", "weibo.cn")),
        ("youtube", ("youtube.com", "youtu.be")),
    )
    for name, suffixes in mapping:
        if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            return name
    return ""


# --------------------------------------------------------------------- 缓存


def _cache_get(key: str) -> dict | None:
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if not entry:
            return None
        if entry.get("exp", 0) <= now:
            _CACHE.pop(key, None)
            return None
        return entry


def _cache_put(
    key: str, *, result: dict | None = None, error: str = "", error_code: str = ""
) -> None:
    if not key:
        return
    ttl = _TTL_OK if result else _TTL_FAIL
    with _CACHE_LOCK:
        _CACHE[key] = {
            "exp": time.time() + ttl,
            "result": copy.deepcopy(result) if result else None,
            "error": error,
            "error_code": (error_code or "").strip(),
        }
        if len(_CACHE) > 200:
            for stale in sorted(_CACHE, key=lambda k: _CACHE[k].get("exp", 0))[:50]:
                _CACHE.pop(stale, None)


def clear_cache() -> None:
    """测试/调试用。"""
    with _CACHE_LOCK:
        _CACHE.clear()


# --------------------------------------------------------------- 通用归一化


def _last_url(url_list) -> str:
    if isinstance(url_list, str):
        return url_list.strip()
    if not isinstance(url_list, list):
        return ""
    for value in reversed(url_list):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _clean_url(value: str) -> str:
    return unescape(str(value or "")).replace("\\/", "/").strip()


class _Collector:
    def __init__(self) -> None:
        self.urls: list[tuple[str, str]] = []
        self.seen: set[str] = set()
        self.thumbs: list[str] = []
        self.title = ""

    def add(self, url, kind: str) -> None:
        value = _clean_url(url)
        if not value.lower().startswith(("http://", "https://")):
            return
        if kind == "video" and not _MEDIA_URL_RE.search(value):
            return
        if value in self.seen:
            return
        self.seen.add(value)
        self.urls.append((value, kind))

    def thumb(self, url) -> None:
        value = _clean_url(url)
        if value.lower().startswith(("http://", "https://")) and value not in self.thumbs:
            self.thumbs.append(value)


def _normalize_douyin(obj) -> dict | None:
    """递归归一化抖音数据结构，返回 {title, items}；找不到媒体返回 None。"""
    collector = _Collector()

    def walk(node, depth: int = 0) -> None:
        if depth > 40 or node is None:
            return
        if isinstance(node, dict):
            if not collector.title:
                for key in ("desc", "title", "item_title"):
                    value = node.get(key)
                    if isinstance(value, str) and value.strip():
                        collector.title = re.sub(r"\s+", " ", value.strip())[:120]
                        break
            for key in ("play_addr", "download_addr", "play_addr_h264", "play_addr_265"):
                value = node.get(key)
                if isinstance(value, dict):
                    collector.add(_last_url(value.get("url_list")), "video")
            for key in ("video_url", "play_url"):
                value = node.get(key)
                if isinstance(value, str):
                    collector.add(value, "video")
            images = node.get("images")
            if isinstance(images, list):
                for image in images:
                    if isinstance(image, dict):
                        collector.add(_last_url(image.get("url_list")), "image")
                        if image.get("uri") and not image.get("url_list"):
                            collector.add(str(image.get("uri")), "image")
                    elif isinstance(image, str):
                        collector.add(image, "image")
            for key in ("cover", "origin_cover", "dynamic_cover"):
                value = node.get(key)
                if isinstance(value, dict):
                    collector.thumb(_last_url(value.get("url_list")))
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    def scan_any(node, depth: int = 0) -> None:
        """兜底：任意 mp4/m3u8 字符串。"""
        if depth > 40:
            return
        if isinstance(node, dict):
            for value in node.values():
                scan_any(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                scan_any(value, depth + 1)
        elif isinstance(node, str):
            value = _clean_url(node)
            if _MEDIA_URL_RE.search(value):
                collector.add(value, "video")

    walk(obj)
    if not collector.urls:
        scan_any(obj)
    if not collector.urls:
        return None

    videos = [entry for entry in collector.urls if entry[1] == "video"]
    images = [entry for entry in collector.urls if entry[1] == "image"]
    ordered = videos + images
    thumb = collector.thumbs[0] if collector.thumbs else ""
    items = []
    for url, kind in ordered[:60]:
        items.append({"url": url, "thumb": thumb if kind == "video" else "",
                      "kind": kind, "source": "douyin"})
    return {"title": collector.title or "抖音作品", "items": items}


def _normalize_api(obj) -> dict | None:
    """第三方接口通用归一化：play_addr.url_list、video_url、url(.mp4)、images[].url_list。"""
    return _normalize_douyin(obj)


# ----------------------------------------------------------------- 直连解析


def _parse_json_blobs(html: str) -> list:
    blobs: list = []
    for match in _JSON_SCRIPT_RE.finditer(html or ""):
        raw = (match.group(1) or "").strip()
        if not raw:
            continue
        for candidate in (raw, unquote(raw)):
            try:
                blobs.append(json.loads(candidate))
                break
            except Exception:
                continue

    for marker in ("_ROUTER_DATA", "RENDER_DATA", "__INITIAL_STATE__"):
        parsed = _balanced_json_after(html or "", marker)
        if parsed is not None:
            blobs.append(parsed)
    return blobs


def _balanced_json_after(text: str, marker: str):
    index = text.find(marker)
    if index < 0:
        return None
    start = text.find("{", index)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                snippet = text[start : position + 1]
                try:
                    return json.loads(snippet)
                except Exception:
                    return None
    return None


def _html_title(html: str) -> str:
    match = _TITLE_RE.search(html or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", unescape(match.group(1))).strip()[:120]


def _set_bridge_reason(text: str) -> None:
    _BRIDGE_LOCAL.reason = (text or "").strip()


def _bridge_reason() -> str:
    return str(getattr(_BRIDGE_LOCAL, "reason", "") or "")


def _set_bridge_code(text: str) -> None:
    _BRIDGE_LOCAL.code = (text or "").strip()


def _bridge_code() -> str:
    return str(getattr(_BRIDGE_LOCAL, "code", "") or "")


def _is_android_platform() -> bool:
    if sys.platform == "android":
        return True
    try:
        from . import media

        return media.is_android()
    except Exception:
        return False


def _android_bridge_resolve(page_url: str, timeout_ms: int = 25000) -> dict | None:
    """Android 原生桥；仅 Android 调用 java，其他平台返回 None。"""
    _set_bridge_reason("")
    _set_bridge_code("")
    if not page_url or not _is_android_platform():
        return None
    try:
        from java import jclass  # type: ignore

        raw = jclass(_DOUYIN_BRIDGE_CLASS).resolve(str(page_url), int(timeout_ms))
    except Exception:
        _set_bridge_reason("安卓解析桥调用失败")
        _set_bridge_code("PAGE_LOAD_FAILED")
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        _set_bridge_reason("安卓解析桥返回的数据看不懂")
        _set_bridge_code("PAGE_LOAD_FAILED")
        return None
    if not isinstance(payload, dict):
        _set_bridge_reason("安卓解析桥返回的数据看不懂")
        _set_bridge_code("PAGE_LOAD_FAILED")
        return None
    if not payload.get("ok"):
        reason = str(payload.get("error") or "").strip() or "安卓解析桥没有解析出媒体"
        _set_bridge_reason(reason[:120])
        _set_bridge_code(str(payload.get("error_code") or "").strip())
        return None

    raw_items = payload.get("items")
    items = [entry for entry in raw_items if isinstance(entry, dict)] if isinstance(raw_items, list) else []
    video_url = str(payload.get("video_url") or "").strip()
    if not items and not video_url:
        _set_bridge_reason("安卓解析桥没有返回可下载媒体")
        _set_bridge_code("MEDIA_UNSUPPORTED")
        return None
    if video_url:
        payload["video_url"] = video_url
    payload["items"] = items
    return payload


def _bridge_result(page_url: str, payload: dict) -> dict:
    raw_items = [entry for entry in (payload.get("items") or []) if isinstance(entry, dict)]
    title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()
    if not title:
        title = "抖音作品" if raw_items and any(
            (entry.get("kind") or "video") != "video" for entry in raw_items
        ) else "抖音视频"
    stem = net.clean_filename(title, fallback="抖音作品", max_len=76)
    referer = str(payload.get("referer") or "").strip()
    if not referer.lower().startswith(("http://", "https://")):
        referer = _DOUYIN_REFERER
    cover = str(payload.get("cover") or "").strip()
    total = len(raw_items) or 1
    items: list[dict] = []
    for index, entry in enumerate(raw_items, 1):
        media_url = str(entry.get("url") or "").strip()
        if not media_url:
            continue
        kind = "video" if (entry.get("kind") or "video") == "video" else "image"
        if kind == "video":
            ext = "mp4"
            name = f"{stem}.mp4" if total == 1 else f"{stem}_{index:02d}.mp4"
            item = {
                "url": media_url,
                "thumb": str(entry.get("thumb") or cover).strip(),
                "kind": "video",
                "ext": ext,
                "name": name,
                "w": int(entry.get("width") or entry.get("w") or 0),
                "h": int(entry.get("height") or entry.get("h") or 0),
                "size": int(entry.get("size") or 0),
                "watermark": bool(entry.get("watermark", payload.get("watermark"))),
            }
            fallback = str(entry.get("fallback_url") or "").strip()
            if fallback:
                item["fallback_url"] = fallback
        else:
            _s, ext = net.filename_from(media_url, "", fallback="image")
            ext = (ext or "jpg").lower()
            name = f"{stem}_{index:02d}.{ext}" if total > 1 else f"{stem}.{ext}"
            item = {
                "url": media_url,
                "thumb": str(entry.get("thumb") or media_url).strip(),
                "kind": "image",
                "ext": ext,
                "name": name,
            }
            live = entry.get("live_photo")
            if isinstance(live, dict) and str(live.get("url") or "").strip():
                item["live_url"] = str(live["url"]).strip()
        item.update(
            source="douyin",
            via="dy",
            referer=referer,
            page=page_url,
        )
        items.append(item)

    # 兼容旧桥：只有 video_url 时按单视频处理。
    if not items:
        video_url = str(payload.get("video_url") or "").strip()
        if video_url:
            items.append(
                {
                    "url": video_url,
                    "thumb": cover,
                    "kind": "video",
                    "ext": "mp4",
                    "name": f"{stem}.mp4",
                    "via": "dy",
                    "referer": referer,
                    "watermark": bool(payload.get("watermark")),
                    "source": "douyin",
                    "page": page_url,
                }
            )

    note = "抖音原生解析" if str(payload.get("source") or "") == "native" else "抖音安卓桥"
    return {
        "title": title[:120],
        "engine": "douyin",
        "note": note,
        "items": items,
    }


def _follow_redirects(url: str, max_hops: int = 3) -> str:
    """手工跟随少量 302，用于解析 v.douyin.com 短链。"""
    current = url
    for _ in range(max(0, int(max_hops))):
        try:
            resp = net.session().get(
                current,
                headers={"User-Agent": net.UA_IPHONE, "Accept": "*/*"},
                timeout=10,
                allow_redirects=False,
                stream=True,
            )
        except Exception:
            return current
        try:
            if resp.status_code not in (301, 302, 303, 307, 308):
                return current
            location = resp.headers.get("Location")
            if not location:
                return current
            current = urljoin(current, location)
        finally:
            resp.close()
    return current


def douyin_link_from_text(text: str) -> str:
    """从分享文案/裸链接里取出抖音链接；没有返回空串。"""
    raw = (text or "").strip().replace("\\/", "/")
    if not raw:
        return ""
    match = _DOUYIN_TEXT_RE.search(raw)
    if match:
        link = match.group(0)
        if not link.lower().startswith(("http://", "https://")):
            link = "https://" + link.lstrip("/")
        return link
    link = net.first_url(raw)
    if link.lower().startswith(("http://", "https://")) and is_douyin(link):
        return link
    bare = _BARE_ID_RE.fullmatch(link.strip())
    if bare:
        return f"https://www.douyin.com/video/{bare.group(1)}"
    return ""


def douyin_page_url(text: str) -> str:
    """把分享文案/短链归一成可解析的作品页 URL。"""
    link = douyin_link_from_text(text)
    if not link:
        return ""
    match = _PAGE_KIND_RE.search(link)
    if match:
        return f"https://www.douyin.com/{match.group(1)}/{match.group(2)}"
    if not link.lower().startswith(("http://", "https://")):
        bare = _BARE_ID_RE.fullmatch(link.strip())
        return f"https://www.douyin.com/video/{bare.group(1)}" if bare else ""
    if "v.douyin.com" in net.host_of(link):
        link = _follow_redirects(link, 3)
    match = _PAGE_KIND_RE.search(link)
    if match:
        return f"https://www.douyin.com/{match.group(1)}/{match.group(2)}"
    return link if is_douyin(link) else ""


def douyin_direct(url: str) -> dict | None:
    """第一层：直连分享页解析。找不到返回 None；网络错误向上抛。"""
    raw = (url or "").strip()
    link = net.first_url(raw) or raw
    video_id = ""
    match = _VIDEO_ID_RE.search(link)
    if match:
        video_id = match.group(1)
    elif not link.lower().startswith("http"):
        bare = _BARE_ID_RE.search(link)
        video_id = bare.group(1) if bare else ""

    html = ""
    if not video_id:
        if not link.lower().startswith("http"):
            return None
        status, body, final_url = net.http_get(link, timeout=12)
        html = body.decode("utf-8", errors="replace") if body else ""
        link = final_url or link
        match = _VIDEO_ID_RE.search(link)
        if match:
            video_id = match.group(1)
        else:
            trimmed = _BARE_ID_RE.search(link.replace("/", " "))
            if trimmed:
                video_id = trimmed.group(1)

    if video_id:
        page_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
        try:
            status, body, final_url = net.http_get(page_url, timeout=12)
            if body:
                html = body.decode("utf-8", errors="replace")
                link = final_url or page_url
        except Exception:
            if not html:
                raise

    if not html:
        return None

    merged_items: list[dict] = []
    seen_urls: set[str] = set()
    title = ""
    for blob in _parse_json_blobs(html):
        normalized = _normalize_douyin(blob)
        if not normalized:
            continue
        title = title or normalized.get("title") or ""
        for item in normalized["items"]:
            if item["url"] in seen_urls:
                continue
            seen_urls.add(item["url"])
            merged_items.append(item)
    if merged_items:
        return {"title": title or _html_title(html) or "抖音作品",
                "items": merged_items, "engine": "douyin", "note": "抖音直连"}

    # 最后再从整页文本里捞一次媒体直链
    text = html.replace("\\/", "/")
    fallback_items = []
    for match in _MEDIA_TEXT_RE.finditer(text):
        url = _clean_url(match.group(0))
        if url in seen_urls:
            continue
        seen_urls.add(url)
        fallback_items.append(
            {"url": url, "thumb": "", "kind": "video", "source": "douyin"}
        )
    if fallback_items:
        return {"title": _html_title(html) or "抖音作品", "items": fallback_items,
                "engine": "douyin", "note": "抖音直连"}
    return None


# ------------------------------------------------------------- 第三方接口链


def douyin_api(api_template: str, url: str, timeout: float = 8) -> dict:
    """GET 一条第三方接口并归一化；失败抛 EngineError（中文原因）。"""
    template = (api_template or "").strip()
    if not template:
        raise EngineError("接口模板是空的")
    encoded = quote(net.first_url(url) or url, safe="")
    if "{url}" in template:
        target = template.replace("{url}", encoded)
    else:
        target = template + encoded

    try:
        status, body, _final = net.http_get(target, timeout=timeout)
    except Exception as exc:
        text = str(exc).lower()
        if "timed out" in text or "timeout" in text:
            raise EngineError("接口超时") from exc
        raise EngineError("接口连不上") from exc
    if status >= 400:
        raise EngineError(f"接口返回 HTTP {status}")
    if not body:
        raise EngineError("接口没有返回内容")
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        raise EngineError("接口返回的不是数据")

    normalized = _normalize_api(payload)
    if not normalized:
        raise EngineError("接口没有可下载地址")
    title = normalized.get("title") or _html_title(
        body.decode("utf-8", errors="replace")
    )
    normalized["title"] = title or "抖音作品"
    normalized["engine"] = "douyin"
    normalized["note"] = "抖音第三方解析"
    return normalized


def _summarize_attempts(api_reasons: list[str]) -> str:
    if not api_reasons:
        return "第三方接口无数据"
    unique: list[str] = []
    for reason in api_reasons:
        if reason and reason not in unique:
            unique.append(reason)
    if len(unique) == 1:
        return f"第三方{unique[0]}"
    if len(api_reasons) > 1:
        return f"{len(api_reasons)} 个第三方接口都失败（{unique[0]}）"
    return "第三方接口无数据"


def resolve_douyin(
    url: str, cfg: dict | None = None, progress=None, use_cache: bool = True
) -> dict:
    """抖音：Android 原生桥 → 直连 → yt-dlp → 可配置 API 链。"""
    raw = (url or "").strip()
    if not raw:
        raise EngineError("抖音链接是空的")

    cached = _cache_get(raw) if use_cache else None
    if cached:
        if cached.get("error"):
            raise EngineError(cached["error"], code=str(cached.get("error_code") or ""))
        if cached.get("result"):
            return copy.deepcopy(cached["result"])

    def report(msg: str) -> None:
        if progress:
            try:
                progress("engine", msg)
            except Exception:
                pass

    attempts: list[str] = []
    page_url = douyin_page_url(raw)
    if (
        not page_url
        and not _BARE_ID_RE.fullmatch(raw)
        and not raw.lower().startswith(("http://", "https://"))
    ):
        raise EngineError("没有找到抖音链接，请粘贴分享文案或作品链接")
    if page_url:
        bridge = _android_bridge_resolve(page_url)
        if isinstance(bridge, bytes):
            bridge = bridge.decode("utf-8", errors="replace")
        if isinstance(bridge, str):
            try:
                bridge = json.loads(bridge)
            except Exception:
                bridge = None
                _set_bridge_reason("安卓解析桥返回的数据看不懂")
        if isinstance(bridge, dict):
            items = bridge.get("items") or []
            has_media = bool(items) or bool(str(bridge.get("video_url") or "").strip())
            if bridge.get("ok") and has_media:
                result = _bridge_result(page_url, bridge)
                if result.get("items"):
                    _cache_put(raw, result=result)
                    return result
            _set_bridge_reason(
                str(bridge.get("error") or "安卓解析桥没有解析出媒体").strip()[:120]
            )
            _set_bridge_code(str(bridge.get("error_code") or "").strip())
        reason = _bridge_reason()
        if reason:
            attempts.append(reason)

    report("直连解析")
    try:
        direct = douyin_direct(raw)
    except Exception:
        direct = None
    if direct and direct.get("items"):
        _cache_put(raw, result=direct)
        return direct
    attempts.append("直连无数据")

    report("yt-dlp 解析")
    try:
        resolved = ytdlp_resolve(net.first_url(raw) or raw, progress=progress)
        if resolved.get("items"):
            resolved["engine"] = "douyin"
            resolved["note"] = "抖音（yt-dlp）"
            _cache_put(raw, result=resolved)
            return resolved
    except Exception:
        pass
    attempts.append("yt-dlp 无数据")

    report("第三方接口解析")
    apis = list((cfg or config.load_config()).get("douyin_apis") or [])
    api_reasons: list[str] = []
    for template in apis:
        try:
            result = douyin_api(template, raw)
        except EngineError as exc:
            api_reasons.append(str(exc) or "接口失败")
            continue
        if result.get("items"):
            _cache_put(raw, result=result)
            return result
        api_reasons.append("接口无数据")
    attempts.append(_summarize_attempts(api_reasons))

    message = "抖音解析失败：" + " / ".join(attempts)
    code = _bridge_code() or "PARSE_FAILED"
    _cache_put(raw, error=message, error_code=code)
    raise EngineError(message, code=code)


# ------------------------------------------------------------------- yt-dlp


def _yt_dlp():
    try:
        import yt_dlp  # type: ignore
    except Exception as exc:  # pragma: no cover - 依赖缺失
        raise EngineError("yt-dlp 解析失败：组件未安装") from exc
    return yt_dlp


def _ydl_opts(extra: dict | None = None) -> dict:
    from . import media

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 1,
    }
    ffmpeg_dir = media.ffmpeg_dir()
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    if extra:
        opts.update(extra)
    return opts


def _ytdlp_error(exc: Exception) -> str:
    text = str(exc or "").lower()
    if "unsupported url" in text:
        return "yt-dlp 解析失败：这个链接不受支持"
    if "timed out" in text or "timeout" in text:
        return "yt-dlp 解析失败：连接超时"
    if "sign in" in text or "login" in text or "cookies" in text:
        return "yt-dlp 解析失败：需要登录才能看"
    if "private" in text or "unavailable" in text or "not available" in text:
        return "yt-dlp 解析失败：视频不可用"
    if "403" in text or "forbidden" in text:
        return "yt-dlp 解析失败：被网站拒绝了"
    if "ffmpeg" in text:
        return "yt-dlp 解析失败：缺少 ffmpeg"
    return "yt-dlp 解析失败：视频解析不出来"


_BAD_MEDIA_EXT = {"html", "htm", "mhtml", "xml", "json", "txt", "php", "asp", "aspx", "unknown_video"}


def _looks_media(fmt: dict) -> bool:
    """过滤 yt-dlp 通用提取器把网页本身当成“视频”的情况。"""
    url = str(fmt.get("url") or "")
    ext = str(fmt.get("ext") or "").lower()
    if ext in _BAD_MEDIA_EXT:
        return False
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    if path.endswith((".html", ".htm", ".php", ".asp", ".aspx")):
        return False
    return True


def _pick_best_format(formats: list[dict]) -> dict | None:
    best = None
    best_score = None
    for fmt in formats or []:
        url = fmt.get("url")
        if not url or not _looks_media(fmt):
            continue
        if fmt.get("format_note") and "storyboard" in str(fmt["format_note"]).lower():
            continue
        vcodec = fmt.get("vcodec") or "none"
        acodec = fmt.get("acodec") or "none"
        if vcodec == "none" and acodec == "none":
            continue
        height = int(fmt.get("height") or 0)
        tbr = float(fmt.get("tbr") or 0)
        # 必须先保证是视频流：B 站 DASH 的音频流 acodec 有值但 height=0，
        # 之前的排序会把纯音频当成“最佳视频”。
        score = (1 if vcodec != "none" else 0,
                 height,
                 1 if (vcodec != "none" and acodec != "none") else 0,
                 1 if url.lower().split("?")[0].endswith((".mp4", ".m3u8")) else 0,
                 tbr)
        if best_score is None or score > best_score:
            best_score = score
            best = fmt
    return best


def ytdlp_resolve(url: str, progress=None) -> dict:
    """yt-dlp 解析，返回 {title, items, engine:"ytdlp"}。"""
    link = net.first_url(url) or url
    if not link:
        raise EngineError("yt-dlp 解析失败：链接是空的")
    yt_dlp = _yt_dlp()
    if progress:
        try:
            progress("engine", "yt-dlp 解析")
        except Exception:
            pass
    try:
        with yt_dlp.YoutubeDL(_ydl_opts({"skip_download": True})) as ydl:
            info = ydl.extract_info(link, download=False)
    except Exception as exc:
        raise EngineError(_ytdlp_error(exc)) from exc

    if not info:
        raise EngineError("yt-dlp 解析失败：没有拿到视频信息")
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [entry for entry in (info.get("entries") or []) if entry]
        if not entries:
            raise EngineError("yt-dlp 解析失败：列表里没有视频")
        info = entries[0]

    title = re.sub(r"\s+", " ", str(info.get("title") or "")).strip()[:120] or "视频"
    thumb = str(info.get("thumbnail") or "")
    if not thumb:
        thumbnails = info.get("thumbnails") or []
        if thumbnails and isinstance(thumbnails[-1], dict):
            thumb = str(thumbnails[-1].get("url") or "")

    formats = info.get("formats") or []
    best = _pick_best_format(formats)
    if best:
        media_url = best.get("url") or ""
        ext = str(best.get("ext") or info.get("ext") or "mp4").lower()
        kind = "image" if ext in ("jpg", "jpeg", "png", "webp", "gif") else "video"
    else:
        media_url = str(info.get("url") or "")
        ext = str(info.get("ext") or "mp4").lower()
        if ext in _BAD_MEDIA_EXT:
            media_url = ""
        kind = "image" if ext in ("jpg", "jpeg", "png", "webp", "gif") else "video"
    if not media_url:
        raise EngineError("yt-dlp 解析失败：没有可下载地址")

    return {
        "title": title,
        "items": [
            {
                "url": media_url,
                "thumb": thumb,
                "kind": kind,
                "source": "ytdlp",
                "w": int(best.get("width") or 0) if best else 0,
                "h": int(best.get("height") or 0) if best else 0,
                "size": int(best.get("filesize") or best.get("filesize_approx") or 0) if best else 0,
            }
        ],
        "engine": "ytdlp",
        "note": "yt-dlp",
    }


def _download_format(url: str, path, headers: dict | None, progress, label: str) -> None:
    response = net.stream_get(url, timeout=(20, 120), headers=headers or {})
    try:
        if response.status_code >= 400:
            raise EngineError(f"yt-dlp 下载失败：{label}下载被拒绝（HTTP {response.status_code}）")
        total = 0
        try:
            total = int(response.headers.get("Content-Length") or 0)
        except Exception:
            total = 0
        written = 0
        with open(path, "wb") as handle:
            for chunk in response.iter_content(131072):
                if not chunk:
                    continue
                handle.write(chunk)
                written += len(chunk)
                if progress and total:
                    try:
                        progress("download", f"下载{label} {int(written * 100 / total)}%")
                    except Exception:
                        pass
    finally:
        response.close()


def _pick_streams(info: dict) -> tuple[dict | None, dict | None, dict | None]:
    formats = info.get("formats") or []
    muxed = []
    video_only = []
    audio_only = []
    for fmt in formats:
        if not fmt.get("url"):
            continue
        if fmt.get("format_note") and "storyboard" in str(fmt["format_note"]).lower():
            continue
        vcodec = fmt.get("vcodec") or "none"
        acodec = fmt.get("acodec") or "none"
        height = int(fmt.get("height") or 0)
        abr = float(fmt.get("abr") or 0)
        tbr = float(fmt.get("tbr") or 0)
        if vcodec != "none" and acodec != "none":
            muxed.append(((height, tbr), fmt))
        elif vcodec != "none":
            video_only.append(((height, tbr), fmt))
        elif acodec != "none":
            audio_only.append(((abr, tbr), fmt))
    pick = lambda pairs: max(pairs, key=lambda item: item[0])[1] if pairs else None
    return pick(muxed), pick(video_only), pick(audio_only)


def ytdlp_download(url: str, out_dir, progress=None) -> tuple[str, str]:
    """下载最佳视频（桌面用 yt-dlp 合并；Android 走 media.merge）。"""
    from pathlib import Path

    from . import media

    link = net.first_url(url) or url
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    yt_dlp = _yt_dlp()

    if media.is_android() or not media.ffmpeg_available():
        return _ytdlp_download_manual(link, out_path, progress)

    before = {entry.name for entry in out_path.iterdir() if entry.is_file()}
    opts = _ydl_opts(
        {
            "outtmpl": str(out_path / "%(title).60B_%(id)s.%(ext)s"),
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([link])
    except Exception as exc:
        raise EngineError(_ytdlp_error(exc)) from exc

    candidates = [
        entry
        for entry in out_path.iterdir()
        if entry.is_file()
        and entry.name not in before
        and entry.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3")
    ]
    if not candidates:
        candidates = [entry for entry in out_path.iterdir() if entry.is_file()]
    if not candidates:
        raise EngineError("yt-dlp 下载失败：没有生成文件")
    candidates.sort(
        key=lambda entry: (entry.suffix.lower() == ".mp4", entry.stat().st_size),
        reverse=True,
    )
    best = candidates[0]
    return str(best), best.name


def _ytdlp_download_manual(link: str, out_path, progress=None) -> tuple[str, str]:
    """无 ffmpeg 二进制时（Android/未装 ffmpeg）：分别下载视频与音频再 media.merge。"""
    from . import media

    yt_dlp = _yt_dlp()
    try:
        with yt_dlp.YoutubeDL(_ydl_opts({"skip_download": True})) as ydl:
            info = ydl.extract_info(link, download=False)
    except Exception as exc:
        raise EngineError(_ytdlp_error(exc)) from exc
    if info.get("_type") in ("playlist", "multi_video") and info.get("entries"):
        entries = [entry for entry in info["entries"] if entry]
        if not entries:
            raise EngineError("yt-dlp 下载失败：列表里没有视频")
        info = entries[0]
    if not info:
        raise EngineError("yt-dlp 下载失败：没有拿到视频信息")

    title = re.sub(r"\s+", " ", str(info.get("title") or "video")).strip()
    safe_title = net.clean_filename(title, fallback="video", max_len=60)
    headers = info.get("http_headers") or {}

    muxed, video_only, audio_only = _pick_streams(info)
    if muxed and not (video_only and audio_only):
        fmt = muxed
        ext = str(fmt.get("ext") or "mp4")
        target = out_path / f"{safe_title}.{ext}"
        _download_format(fmt["url"], target, headers, progress, "视频")
        return str(target), target.name

    if not video_only:
        raise EngineError("yt-dlp 下载失败：没有找到视频流")
    video_ext = str(video_only.get("ext") or "mp4")
    video_path = out_path / f"{safe_title}.video.{video_ext}"
    _download_format(video_only["url"], video_path, headers, progress, "视频")

    if not audio_only:
        final = out_path / f"{safe_title}.{video_ext}"
        video_path.replace(final)
        return str(final), final.name

    audio_ext = str(audio_only.get("ext") or "m4a")
    audio_path = out_path / f"{safe_title}.audio.{audio_ext}"
    _download_format(audio_only["url"], audio_path, headers, progress, "音频")

    merged = out_path / f"{safe_title}.mp4"
    media.merge(str(video_path), str(audio_path), str(merged))
    for temp in (video_path, audio_path):
        try:
            temp.unlink()
        except OSError:
            pass
    return str(merged), merged.name
