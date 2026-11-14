"""Safe metadata resolver for publicly shared NetEase Cloud Music song links.

This intentionally does not request audio stream URLs or attempt to bypass
subscriptions, DRM, or regional access controls. Playback is delegated to the
official embedded player in the WebView.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from . import net

_ALLOWED_HOSTS = {"163cn.tv", "music.163.com", "y.music.163.com"}
_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_HTML = 2 * 1024 * 1024


def _trusted_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname is not None
            and parts.hostname.lower() in _ALLOWED_HOSTS
            and parts.port in (None, 443)
            and not parts.username
            and not parts.password
        )
    except ValueError:
        return False


def _song_id(url: str) -> str:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in {"music.163.com", "y.music.163.com"}:
        return ""
    if not re.search(r"(?:^|/)song(?:/|$)", parts.path, re.I):
        return ""
    candidate = (parse_qs(parts.query).get("id") or [""])[0]
    return candidate if re.fullmatch(r"\d{1,14}", candidate) else ""


def is_music_link(url: str) -> bool:
    """Whether a URL belongs to the NetEase share/site family we handle."""
    if not _trusted_url(url):
        return False
    host = (urlsplit(url).hostname or "").lower()
    # Route all NetEase pages away from generic yt-dlp fallback (which may
    # probe stream tiers); unsupported albums/playlists receive a clear notice.
    return host in {"163cn.tv", "music.163.com", "y.music.163.com"}


def _get_song_page(url: str) -> tuple[str, bytes]:
    current = url
    for _ in range(6):
        if not _trusted_url(current):
            raise ValueError("音乐链接跳转到了不受支持的站点")
        response = requests.get(
            current,
            headers={"User-Agent": net.UA_IPHONE, "Accept": "text/html,application/xhtml+xml"},
            timeout=(8, 15),
            allow_redirects=False,
        )
        if response.status_code in _REDIRECTS:
            location = response.headers.get("Location", "")
            if not location:
                raise ValueError("音乐分享链接没有有效的跳转地址")
            current = urljoin(current, location)
            continue
        if response.status_code >= 400:
            raise ValueError(f"网易云音乐页面返回 HTTP {response.status_code}")
        if len(response.content) > _MAX_HTML:
            raise ValueError("音乐页面内容过大，已停止解析")
        song_id = _song_id(current)
        if not song_id:
            # Short links must resolve to a single-track canonical page.
            soup = BeautifulSoup(response.content, "html.parser")
            canonical = soup.find("link", rel=lambda v: v and "canonical" in v)
            canonical_url = canonical.get("href", "") if canonical else ""
            song_id = _song_id(urljoin(current, canonical_url)) if canonical_url else ""
        if not song_id:
            raise ValueError("暂不支持该网易云音乐链接（目前支持单曲分享链接）")
        return current, response.content
    raise ValueError("音乐分享链接重定向次数过多")


def resolve_song(url: str) -> dict:
    """Return one metadata-only music result, never an audio download URL."""
    if not is_music_link(url):
        raise ValueError("这不是受支持的网易云音乐单曲链接")
    final_url, body = _get_song_page(url)
    soup = BeautifulSoup(body, "html.parser")
    song_id = _song_id(final_url)
    canonical = f"https://music.163.com/song?id={song_id}"

    def meta(*selectors: str) -> str:
        for selector in selectors:
            tag = soup.select_one(selector)
            if tag:
                value = tag.get("content") or tag.get("href") or ""
                if value.strip():
                    return value.strip()
        return ""

    page_title = meta('meta[property="og:title"]', 'meta[name="title"]')
    if not page_title and soup.title:
        page_title = soup.title.get_text(" ", strip=True)
    page_title = re.sub(r"\s*-\s*单曲\s*-\s*网易云音乐\s*$", "", page_title).strip()
    if page_title.endswith(" - 网易云音乐"):
        page_title = page_title[:-8].strip()
    title, artist = page_title, ""
    if " - " in page_title:
        title, artist = page_title.split(" - ", 1)
    title = title or f"网易云音乐单曲 {song_id}"

    cover = meta('meta[property="og:image"]', 'meta[name="twitter:image"]')
    if cover:
        cover = urljoin(final_url, cover)
        cover_parts = urlsplit(cover)
        if cover_parts.scheme == "http" and (cover_parts.hostname or "").lower().endswith(".music.126.net"):
            cover = cover_parts._replace(scheme="https").geturl()
            cover_parts = urlsplit(cover)
        cover_host = (cover_parts.hostname or "").lower()
        if (
            cover_parts.scheme != "https"
            or not cover_host
            or not (cover_host.endswith(".music.126.net") or cover_host in {"music.163.com", "music.126.net"})
            or cover_parts.username
            or cover_parts.password
            or cover_parts.port not in (None, 443)
        ):
            cover = ""

    return {
        "ok": True,
        "url": url,
        "title": title,
        "engine": "netease-metadata",
        "note": "已解析歌曲信息；试听由网易云音乐官方播放器提供，不提供付费或受保护音源下载",
        "items": [{
            "id": f"netease-{song_id}",
            "kind": "music",
            "name": title,
            "artist": artist,
            "ext": "",
            "url": canonical,
            "official_url": canonical,
            "play_url": f"https://music.163.com/outchain/player?type=2&id={song_id}&auto=0&height=66",
            "thumb": cover,
            "referer": canonical,
            "downloadable": False,
            "source": "netease",
        }],
        "groups": {"image": [], "video": [], "music": [0]},
    }
