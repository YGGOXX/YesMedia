"""HTML 媒体提取与扫描编排。"""

from __future__ import annotations

import re
import threading
import time
from html import unescape
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from . import engines, net

IMG_EXT = {
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "avif", "heic", "jfif", "tif", "tiff",
}
VID_EXT = {"mp4", "m3u8", "webm", "mov", "m4v", "mkv", "flv", "ts", "m3u", "avi"}
AUD_EXT = {"mp3", "m4a", "aac", "wav", "ogg", "flac", "opus"}
BAD_WORDS = (
    "favicon", "logo", "avatar", "icon", "sprite", "blank", "1x1", "pixel.gif",
    "spacer", "loading.gif", "placeholder", "transparent.gif", "spinner",
)

_SRCSET_SPLIT = re.compile(r"\s*,\s*")
_BG_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_MEDIA_IN_SCRIPT = re.compile(
    r"""(?:https?:\\?/\\?/[^\s"'\\<>]+?|(?<![\w.])/[^\s"'\\<>]+?)\.(?:m3u8|mp4|webm|mov)"""
    r"""(?:\?[^\s"'\\<>]*)?(?![\w.])""",
    re.IGNORECASE,
)


class ScanError(Exception):
    """扫描失败，消息直接展示给用户（中文），code 用于前端区分动作。"""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = (code or "").strip()


def _ext_of(url: str) -> str:
    path = urlsplit(url or "").path
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    ext = name.rsplit(".", 1)[-1].strip().lower()
    if 1 <= len(ext) <= 5 and re.fullmatch(r"[a-z0-9]+", ext):
        return ext
    return ""


def _is_bad(url: str) -> bool:
    low = url.lower()
    return any(word in low for word in BAD_WORDS)


def _abs(base: str, raw: str) -> str:
    value = unescape((raw or "").strip()).strip("\"' \t\r\n")
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    low = value.lower()
    if low.startswith(("data:", "blob:", "javascript:", "about:", "mailto:", "#")):
        return ""
    absolute = urljoin(base, value)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    # 去掉 fragment，便于去重
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _pick_srcset(value: str) -> str:
    """srcset 里取描述符最大的那个 URL。"""
    best_url = ""
    best_score = -1.0
    for part in _SRCSET_SPLIT.split(value or ""):
        part = part.strip()
        if not part:
            continue
        pieces = part.split()
        url = pieces[0]
        score = 1.0
        if len(pieces) > 1:
            descriptor = pieces[-1].lower()
            try:
                if descriptor.endswith("w"):
                    score = float(descriptor[:-1])
                elif descriptor.endswith("x"):
                    score = float(descriptor[:-1]) * 1000
            except ValueError:
                score = 1.0
        if score > best_score:
            best_score = score
            best_url = url
    return best_url


def extract_media(html: str, base_url: str) -> list[dict]:
    """从 HTML 提取候选媒体，返回 [{url, thumb, kind, source}]。"""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    page_base = base_url
    base_tag = soup.find("base", href=True)
    if base_tag:
        page_base = _abs(base_url, base_tag["href"]) or base_url

    candidates: list[dict] = []
    seen: set[str] = set()

    def add(raw_url: str, kind: str = "", thumb: str = "", source: str = "") -> None:
        absolute = _abs(page_base, raw_url)
        if not absolute or _is_bad(absolute):
            return
        ext = _ext_of(absolute)
        if ext in AUD_EXT:
            return
        if not kind:
            kind = "video" if ext in VID_EXT else "image"
        if absolute in seen:
            return
        seen.add(absolute)
        thumb_abs = _abs(page_base, thumb) if thumb else ""
        if thumb_abs and _is_bad(thumb_abs):
            thumb_abs = ""
        candidates.append(
            {"url": absolute, "thumb": thumb_abs, "kind": kind, "source": source or "html"}
        )

    # <img> 各种懒加载属性
    for tag in soup.find_all("img"):
        src = tag.get("src") or ""
        add(src, source="img")
        for attr in ("data-src", "data-original", "data-lazy-src", "data-url", "data-actualsrc"):
            if tag.get(attr):
                add(tag[attr], source="img")
        if tag.get("srcset"):
            add(_pick_srcset(tag["srcset"]), source="img")

    # <picture><source>
    for tag in soup.find_all("source"):
        if tag.get("srcset"):
            add(_pick_srcset(tag["srcset"]), source="picture")
        if tag.get("src"):
            add(tag["src"], source="picture")

    # <video> / <audio>
    for tag in soup.find_all("video"):
        poster = tag.get("poster") or ""
        src = tag.get("src") or tag.get("data-src") or ""
        if src:
            add(src, kind="video", thumb=poster, source="video")
        else:
            # 只有 poster 没有视频地址时，poster 本身就是可下载图片
            add(poster, kind="image", source="video")
        for child in tag.find_all("source"):
            if child.get("src"):
                add(child["src"], kind="video", thumb=poster, source="video")

    # og / twitter 元信息
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").strip().lower()
        content = tag.get("content") or ""
        if not content:
            continue
        if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image",
                   "twitter:image:src", "image"):
            add(content, kind="image", source="meta")
        elif key in ("og:video", "og:video:url", "og:video:secure_url", "og:video:iframe"):
            add(content, kind="video", source="meta")

    # <link rel="image_src">
    for tag in soup.find_all("link"):
        if "image_src" in (tag.get("rel") or []):
            add(tag.get("href") or "", kind="image", source="link")

    # 内联 style / <style> 背景图
    style_texts = [tag.get("style") or "" for tag in soup.find_all(style=True)]
    style_texts += [tag.string or "" for tag in soup.find_all("style")]
    for css in style_texts:
        for match in _BG_URL.finditer(css):
            add(match.group(2), kind="image", source="css")

    # <a href> 指向图片/视频
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.lower().startswith(("javascript:", "#")):
            continue
        ext = _ext_of(_abs(page_base, href))
        if ext in IMG_EXT:
            add(href, kind="image", source="a")
        elif ext in VID_EXT:
            add(href, kind="video", source="a")

    # 脚本里的 m3u8 / mp4 直链
    for script in soup.find_all("script"):
        text = (script.string or script.get_text() or "").replace("\\/", "/")
        if not text:
            continue
        for match in _MEDIA_IN_SCRIPT.finditer(text):
            add(match.group(0), kind="video", source="script")

    return candidates


def filter_candidates(items: list[dict], page_url: str, allow_third_party: bool) -> list[dict]:
    """同域外链仅当 allow_third_party 或图片时保留。"""
    page_host = net.host_of(page_url)
    result = []
    for item in items:
        kind = item.get("kind") or "image"
        url = item.get("url") or ""
        if kind == "video" and not allow_third_party and page_host:
            host = net.host_of(url)
            if host and not net.same_site(host, page_host):
                continue
        result.append(item)
    return result


def page_title(html: str, fallback: str = "") -> str:
    if not html:
        return fallback
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return fallback
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key in ("og:title", "twitter:title"):
            content = (tag.get("content") or "").strip()
            if content:
                return _clean_title(content)
    if soup.title and soup.title.string:
        return _clean_title(soup.title.string)
    heading = soup.find("h1")
    if heading:
        text = heading.get_text(" ", strip=True)
        if text:
            return _clean_title(text)
    return fallback


def _clean_title(text: str) -> str:
    value = re.sub(r"\s+", " ", unescape(str(text or ""))).strip()
    return value[:120]


def _decode_html(body: bytes) -> str:
    if not body:
        return ""
    head = body[:4096].lower()
    for charset in ("utf-8", "gb18030", "gbk", "big5"):
        marker = f"charset={charset}".encode()
        if marker in head or f'charset="{charset}"'.encode() in head:
            try:
                return body.decode(charset, errors="replace")
            except Exception:
                break
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return body.decode("gb18030", errors="replace")
        except Exception:
            return body.decode("utf-8", errors="replace")


def _fresh_item(url: str, kind: str, thumb: str, source: str, referer: str,
                info: dict, index: int, base_title: str) -> dict:
    stem, ext = net.filename_from(url, info.get("ctype", ""), fallback=base_title or f"item{index}")
    if not ext:
        ext = "mp4" if kind == "video" else "jpg"
    name = f"{stem}.{ext}"
    return {
        "id": f"i{index}",
        "kind": kind,
        "url": url,
        "thumb": thumb,
        "name": name,
        "ext": ext,
        "size": int(info.get("size") or 0),
        "w": int(info.get("w") or 0),
        "h": int(info.get("h") or 0),
        "referer": referer,
        "source": source,
    }


_JUNK_IMG = ("/static/", "jinkela", "gotop", "/bfs/face/")


def _clean_media_url(url: str) -> str:
    """去掉 hdslb 图床的 @尺寸后缀，换成原图；同时去掉 fragment。"""
    try:
        parts = urlsplit(url)
    except Exception:
        return url
    host = (parts.hostname or "").lower()
    path = parts.path
    if host.endswith("hdslb.com") and "@" in path:
        path = path.split("@", 1)[0]
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return url.split("#", 1)[0]


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in candidates:
        url = _clean_media_url(raw.get("url") or "")
        if not url:
            continue
        low = url.lower()
        if any(token in low for token in _JUNK_IMG):
            continue
        if url in seen:
            continue
        seen.add(url)
        item = dict(raw)
        item["url"] = url
        out.append(item)
    return out


def build_items(candidates: list[dict], referer: str = "", progress=None,
                max_items: int = 120, timeout: float = 6,
                title_hint: str = "") -> list[dict]:
    """并发探测候选，生成契约 items。视频在前、图片在后，各自保持原顺序。"""
    selected = _dedupe_candidates(candidates)[:max_items]
    if not selected:
        return []

    results: list[dict] = [{} for _ in selected]
    lock = threading.Lock()
    timeouts: dict[str, int] = {}
    skip_hosts: set[str] = set()

    def worker(index: int, item: dict) -> None:
        url = item.get("url") or ""
        host = net.host_of(url)
        with lock:
            if host and host in skip_hosts:
                results[index] = {"size": 0, "ctype": "", "w": 0, "h": 0}
                return
        try:
            info = net._probe_raw(url, referer=referer, timeout=timeout)
            with lock:
                if host:
                    timeouts[host] = 0
            results[index] = info
        except requests.exceptions.Timeout:
            with lock:
                if host:
                    timeouts[host] = timeouts.get(host, 0) + 1
                    if timeouts[host] >= 3:
                        skip_hosts.add(host)
            results[index] = {"size": 0, "ctype": "", "w": 0, "h": 0}
        except Exception:
            results[index] = {"size": 0, "ctype": "", "w": 0, "h": 0}

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(worker, i, item) for i, item in enumerate(selected)]
        for future in futures:
            try:
                future.result()
            except Exception:
                pass

    videos: list[dict] = []
    images: list[dict] = []
    for index, item in enumerate(selected):
        kind = item.get("kind") or "image"
        target = videos if kind == "video" else images
        target.append(
            {
                "kind": kind,
                "url": item.get("url") or "",
                "thumb": item.get("thumb") or "",
                "source": item.get("source") or "html",
                "info": results[index],
            }
        )

    ordered = videos + images
    items: list[dict] = []
    for index, entry in enumerate(ordered, 1):
        items.append(
            _fresh_item(
                entry["url"], entry["kind"], entry["thumb"], entry["source"],
                referer, entry["info"], index, title_hint,
            )
        )
    return items


def _engine_items(result: dict, referer: str, raw_url: str) -> tuple[str, list[dict], dict]:
    title = _clean_title(result.get("title") or "") or "未命名作品"
    raw_items = list(result.get("items") or [])
    videos = [it for it in raw_items if (it.get("kind") or "video") == "video"]
    images = [it for it in raw_items if (it.get("kind") or "video") != "video"]
    ordered = videos + images
    items: list[dict] = []
    groups = {"image": [], "video": []}
    for index, entry in enumerate(ordered, 1):
        kind = entry.get("kind") or "video"
        source = entry.get("source") or "engine"
        media_url = entry.get("url") or ""
        stem, ext = net.filename_from(media_url, "", fallback=title)
        entry_ext = str(entry.get("ext") or "").lower().lstrip(".")
        if entry_ext:
            ext = entry_ext
        if not ext:
            ext = "mp4" if kind == "video" else "jpg"
        if kind == "video" and source == "ytdlp":
            # 平台视频：保留页面链接，下载时由 /api/yt 重新解析并合并音轨，
            # 直接暴露 m4s 直链会在没有 Referer 时下到 403 的 HTML。
            media_url = raw_url
            ext = "mp4"
        entry_name = str(entry.get("name") or "").strip()
        if entry_name:
            cleaned = net.clean_filename(entry_name, fallback=title, max_len=80)
            if cleaned.lower().endswith("." + ext):
                cleaned = cleaned[: -(len(ext) + 1)]
            stem = net.clean_filename(
                cleaned, fallback=title, max_len=max(12, 80 - len(ext) - 1)
            )
        if len(ordered) > 1:
            stem = net.clean_filename(f"{title}_{index:02d}", fallback=f"item{index}", max_len=72)
        item_referer = str(entry.get("referer") or "").strip()
        if not item_referer:
            item_referer = (
                "https://www.douyin.com/"
                if source == "douyin"
                else (referer or raw_url)
            )
        via = str(entry.get("via") or "").strip()
        if not via and kind == "video" and source == "ytdlp":
            via = "yt"
        if not via and source == "douyin":
            via = "dy"
        item = {
            "id": f"i{index}",
            "kind": kind,
            "url": media_url,
            "thumb": entry.get("thumb") or "",
            "name": f"{stem}.{ext}",
            "ext": ext,
            "size": int(entry.get("size") or 0),
            "w": int(entry.get("w") or 0),
            "h": int(entry.get("h") or 0),
            "referer": item_referer,
            "source": source,
            "via": via,
        }
        page = str(entry.get("page") or "").strip()
        if not page and source == "douyin":
            page = raw_url
        if page:
            item["page"] = page
        if "watermark" in entry:
            item["watermark"] = bool(entry.get("watermark"))
        live_url = str(entry.get("live_url") or "").strip()
        if live_url:
            item["live_url"] = live_url
        fallback_url = str(entry.get("fallback_url") or "").strip()
        if fallback_url:
            item["fallback_url"] = fallback_url
        items.append(item)
        groups.setdefault(kind, []).append(item)
    return title, items, groups


def scan_page(url: str, allow_third_party: bool = False, progress=None) -> dict:
    """扫描页面/平台，返回契约成功体（含 ok=True）。失败抛 ScanError。"""
    raw_input = (url or "").strip()
    if not raw_input:
        raise ScanError("请先粘贴链接", code="NO_LINK")
    douyin_link = engines.douyin_link_from_text(raw_input)
    link = douyin_link or net.first_url(raw_input) or raw_input
    if not link.lower().startswith(("http://", "https://")):
        raise ScanError("链接格式不对，请粘贴 http 或 https 开头的网址", code="INVALID_INPUT")

    def report(stage: str, msg: str) -> None:
        if progress:
            try:
                progress(stage, msg)
            except Exception:
                pass

    report("open", "打开页面")

    html = ""
    final_url = link
    fetch_error = ""
    for ua in ("", net.UA_DESKTOP):
        try:
            status, body, final = net.http_get(link, referer="", timeout=20, ua=ua)
        except Exception:
            fetch_error = "打开页面失败：网络连不上"
            continue
        final_url = final or link
        if body:
            html = _decode_html(body)
        if status < 400:
            fetch_error = ""
            break
        fetch_error = f"打开页面失败（HTTP {status}）"

    candidates = extract_media(html, final_url) if html else []
    candidates = filter_candidates(candidates, final_url, allow_third_party)
    report("probe", f"找到 {len(candidates)} 个候选")

    page_title_cache = page_title(html) if html else ""
    items = build_items(candidates, referer=final_url, progress=progress,
                        title_hint=page_title_cache)
    groups = {
        "image": [item for item in items if item["kind"] == "image"],
        "video": [item for item in items if item["kind"] == "video"],
    }
    title = page_title_cache or net.first_url(raw_input)

    if items:
        platform = engines.is_platform(link)
        if platform in ("bilibili", "weibo", "youtube", "douyin"):
            # 视频站：页面里的图只是封面/预览，优先用平台引擎拿真视频，再补页面图片
            try:
                report("engine", "平台解析")
                if platform == "douyin":
                    resolved = engines.resolve_douyin(raw_input, None, progress=progress)
                    default_note = "抖音解析"
                    default_engine = "douyin"
                else:
                    resolved = engines.ytdlp_resolve(link, progress=progress)
                    default_note = "视频走 yt-dlp"
                    default_engine = "ytdlp"
                etitle, eitems, _egroups = _engine_items(resolved, final_url, raw_input)
                videos = [it for it in eitems if it.get("kind") == "video"]
                if platform == "douyin":
                    # 抖音可能是图集：引擎返回 image 也算成功，不能压缩成单视频。
                    engine_ok = bool(eitems)
                else:
                    engine_ok = bool(videos)
                if engine_ok:
                    if platform == "douyin":
                        merged = eitems + [it for it in items if it.get("kind") == "image"]
                    else:
                        merged = videos + [it for it in items if it.get("kind") == "image"]
                    seen_urls: set[str] = set()
                    deduped = []
                    for it in merged:
                        if it.get("url") in seen_urls:
                            continue
                        seen_urls.add(it.get("url"))
                        deduped.append(it)
                    return {
                        "ok": True,
                        "url": raw_input,
                        "title": etitle or title or "网页媒体",
                        "engine": resolved.get("engine") or default_engine,
                        "note": resolved.get("note") or default_note,
                        "items": deduped,
                        "groups": {
                            "image": [it for it in deduped if it["kind"] == "image"],
                            "video": [it for it in deduped if it["kind"] == "video"],
                        },
                    }
            except engines.EngineError as exc:
                if platform == "douyin":
                    raise ScanError(
                        str(exc).strip() or "抖音解析失败",
                        code=getattr(exc, "code", ""),
                    ) from exc
            except Exception:
                if platform == "douyin":
                    raise ScanError("抖音解析失败，请重试", code="PAGE_LOAD_FAILED")
        return {
            "ok": True,
            "url": raw_input,
            "title": title or "网页媒体",
            "engine": "direct",
            "note": "",
            "items": items,
            "groups": groups,
        }

    # 兜底一：抖音三层链；兜底二：yt-dlp。
    platform = engines.is_platform(link)
    report("engine", "平台解析")
    engine_note = ""
    try:
        if platform == "douyin":
            resolved = engines.resolve_douyin(raw_input, None, progress=progress)
            engine_note = resolved.get("note") or "抖音第三方解析"
        else:
            resolved = engines.ytdlp_resolve(link, progress=progress)
            engine_note = resolved.get("note") or ""
    except engines.EngineError as exc:
        message = str(exc).strip() or "平台解析失败"
        if platform in ("douyin", "bilibili", "weibo", "youtube"):
            raise ScanError(message, code=getattr(exc, "code", "")) from exc
        if fetch_error:
            raise ScanError(fetch_error, code="PAGE_LOAD_FAILED") from exc
        raise ScanError("这个页面没有能下载的东西", code="NO_MEDIA") from exc
    except Exception:
        if fetch_error:
            raise ScanError(fetch_error, code="PAGE_LOAD_FAILED")
        raise ScanError("这个页面没有能下载的东西", code="NO_MEDIA")

    title, engine_items, groups = _engine_items(resolved, final_url, raw_input)
    if not engine_items:
        raise ScanError("这个页面没有能下载的东西", code="NO_MEDIA")
    return {
        "ok": True,
        "url": raw_input,
        "title": title,
        "engine": resolved.get("engine") or "ytdlp",
        "note": engine_note,
        "items": engine_items,
        "groups": groups,
    }
