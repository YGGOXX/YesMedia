"""NetEase share-link metadata parsing; network is mocked in all tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import pytest

from webgrab import extract, music


class Response:
    def __init__(self, status_code, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}


def test_short_share_link_resolves_metadata_only(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url == "https://163cn.tv/bkZmpP2A":
            return Response(302, headers={"Location": "https://y.music.163.com/m/song?id=3455153430"})
        return Response(200, '''<html><head>
          <meta property="og:title" content="再见黑怕 - 法老/冷风 - 单曲 - 网易云音乐">
          <meta property="og:image" content="https://p1.music.126.net/cover.jpg">
        </head></html>'''.encode("utf-8"))

    monkeypatch.setattr(music.requests, "get", fake_get)
    result = music.resolve_song("https://163cn.tv/bkZmpP2A")

    item = result["items"][0]
    assert item["kind"] == "music"
    assert item["name"] == "再见黑怕"
    assert item["artist"] == "法老/冷风"
    assert item["official_url"] == "https://music.163.com/song?id=3455153430"
    assert item["play_url"].startswith("https://music.163.com/outchain/player?")
    assert item["downloadable"] is False
    assert "audio_url" not in item and "stream_url" not in item
    assert all(call[1]["allow_redirects"] is False for call in calls)


def test_rejects_untrusted_redirect_before_fetching(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return Response(302, headers={"Location": "http://127.0.0.1:8000/secret"})

    monkeypatch.setattr(music.requests, "get", fake_get)
    with pytest.raises(ValueError, match="不受支持"):
        music.resolve_song("https://163cn.tv/bkZmpP2A")
    assert calls == ["https://163cn.tv/bkZmpP2A"]


def test_only_netease_single_song_links_are_detected():
    assert music.is_music_link("https://163cn.tv/bkZmpP2A")
    assert music.is_music_link("https://music.163.com/song?id=123")
    assert music.is_music_link("https://music.163.com/playlist?id=123")
    assert not music.is_music_link("https://163cn.tv.evil.example/bkZmpP2A")


def test_scan_page_routes_pasted_share_text_to_music(monkeypatch):
    expected = {"ok": True, "items": [{"kind": "music"}]}
    calls = []

    def fake_resolve(url):
        calls.append(url)
        return expected

    monkeypatch.setattr(music, "resolve_song", fake_resolve)
    result = extract.scan_page("分享歌曲 https://163cn.tv/bkZmpP2A 来自网易云音乐")
    assert result == expected
    assert calls == ["https://163cn.tv/bkZmpP2A"]
