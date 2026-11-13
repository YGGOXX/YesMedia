"""抖音 Android 桥、分享文案归一、/api/dl 过期重解析测试。"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from webgrab import engines, extract, server  # noqa: E402

VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(range(64))
PAGE = "https://www.douyin.com/video/7412345678901234567"


def test_bridge_result_becomes_douyin_item(monkeypatch):
    calls = []

    def fake_bridge(page_url, timeout_ms=25000):
        calls.append((page_url, timeout_ms))
        return {
            "ok": True,
            "video_url": "https://v11-weba.douyinvod.com/abc/video.mp4?token=1",
            "title": '下载/测试:标题*带?"非法"字符<>|' + "长" * 120,
            "cover": "https://p3-sign.douyinpic.com/cover.jpg",
            "referer": "https://m.douyin.com/",
            "watermark": True,
        }

    monkeypatch.setattr(engines, "_android_bridge_resolve", fake_bridge)
    result = engines.resolve_douyin(PAGE, use_cache=False)
    raw_item = result["items"][0]
    assert raw_item["kind"] == "video"
    assert raw_item["ext"] == "mp4"
    assert raw_item["via"] == "dy"
    assert raw_item["referer"] == "https://m.douyin.com/"
    assert raw_item["watermark"] is True
    assert raw_item["page"] == PAGE
    assert raw_item["name"].endswith(".mp4")
    assert calls and calls[0][0] == PAGE

    title, items, groups = extract._engine_items(result, "https://www.douyin.com/", PAGE)
    assert title
    assert groups["video"] == items
    item = items[0]
    assert item["via"] == "dy"
    assert item["referer"] == "https://m.douyin.com/"
    assert item["page"] == PAGE
    assert item["watermark"] is True
    assert item["ext"] == "mp4"
    assert item["name"].endswith(".mp4")
    assert len(item["name"]) <= 80
    assert not any(ch in item["name"] for ch in '\\/:*?"<>|\r\n')


def test_bridge_failure_error_has_all_layers(monkeypatch):
    def fake_bridge(page_url, timeout_ms=25000):
        engines._set_bridge_reason("安卓解析桥没有解析出视频")
        return None

    monkeypatch.setattr(engines, "_android_bridge_resolve", fake_bridge)
    monkeypatch.setattr(engines, "douyin_direct", lambda url: None)
    monkeypatch.setattr(engines, "ytdlp_resolve", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr(
        engines, "douyin_api", lambda *a, **k: (_ for _ in ()).throw(engines.EngineError("接口超时"))
    )
    with pytest.raises(engines.EngineError) as info:
        engines.resolve_douyin(
            "https://www.douyin.com/video/7412345678901234567",
            cfg={"douyin_apis": ["https://api.example/?url={url}"]},
            use_cache=False,
        )
    message = str(info.value)
    assert message.startswith("抖音解析失败：")
    for part in ("安卓解析桥没有解析出视频", "直连无数据", "yt-dlp 无数据", "第三方接口超时"):
        assert part in message


def test_share_text_normalization(monkeypatch):
    text = "7.43 复制打开抖音，看看【某人的作品】 https://www.douyin.com/video/7412345678901234567 好看"
    assert engines.douyin_page_url(text) == PAGE
    assert engines.douyin_link_from_text("hello world") == ""
    assert (
        engines.douyin_link_from_text("复制 v.douyin.com/iRNBho6u/ 打开抖音")
        == "https://v.douyin.com/iRNBho6u"
    )

    seen = []

    def fake_follow(url, max_hops=3):
        seen.append((url, max_hops))
        return PAGE

    monkeypatch.setattr(engines, "_follow_redirects", fake_follow)
    assert engines.douyin_page_url("https://v.douyin.com/iRNBho6u/") == PAGE
    assert seen == [("https://v.douyin.com/iRNBho6u", 3)]


def test_android_bridge_is_noop_off_android(monkeypatch):
    monkeypatch.setattr(engines, "_is_android_platform", lambda: False)
    assert engines._android_bridge_resolve(PAGE) is None
    assert engines._bridge_reason() == ""


def test_scan_page_prefers_douyin_engine_over_cover(monkeypatch):
    html = (
        b"<html><head><title>t</title>"
        b'<meta property="og:image" content="https://p3.douyinpic.com/a.jpg">'
        b"</head></html>"
    )
    monkeypatch.setattr(extract.net, "http_get", lambda *a, **k: (200, html, PAGE))
    monkeypatch.setattr(
        extract.net,
        "_probe_raw",
        lambda *a, **k: {"size": 123, "ctype": "image/jpeg", "w": 10, "h": 20},
    )
    monkeypatch.setattr(
        engines,
        "resolve_douyin",
        lambda *a, **k: {
            "title": "t",
            "engine": "douyin",
            "note": "抖音安卓桥",
            "items": [
                {
                    "url": "https://v11-weba.douyinvod.com/v.mp4",
                    "thumb": "",
                    "kind": "video",
                    "ext": "mp4",
                    "via": "dy",
                    "referer": "https://www.douyin.com/",
                    "page": PAGE,
                    "source": "douyin",
                    "name": "t.mp4",
                }
            ],
        },
    )
    result = extract.scan_page(PAGE)
    assert result["ok"] is True
    assert result["engine"] == "douyin"
    assert [it["kind"] for it in result["items"]] == ["video", "image"]
    assert result["groups"]["video"][0]["via"] == "dy"


def test_non_douyin_text_gives_chinese_error():
    with pytest.raises(engines.EngineError) as info:
        engines.resolve_douyin("复制打开抖音看看", use_cache=False)
    assert "抖音链接" in str(info.value)


class _Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    refs: list[tuple[str, str]] = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        _Upstream.refs.append((path, self.headers.get("Referer", "")))
        if path == "/bad":
            body = b"forbidden"
            self.send_response(403)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path != "/good":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = 0, len(VIDEO) - 1
        status = 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            left, _, right = rng[6:].partition("-")
            start = int(left) if left else 0
            end = int(right) if right else len(VIDEO) - 1
            status = 206
        payload = VIDEO[start : end + 1]
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(VIDEO)}")
        self.end_headers()
        self.wfile.write(payload)


def _start_server(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBGRAB_HOME", str(tmp_path / "home"))
    port = server.start(str(tmp_path / "static"), 0)
    return port


def _post_download(port: int, url: str, page: str, name: str = "test.mp4"):
    target = (
        f"http://127.0.0.1:{port}/api/dl?u={quote(url, safe='')}"
        f"&page={quote(page, safe='')}&via=dy&name={quote(name, safe='')}"
        f"&ref={quote('https://www.douyin.com/', safe='')}"
    )
    return urlopen(Request(target, headers={"Range": "bytes=0-15"}), timeout=15)


def test_download_refreshes_expired_douyin_link(tmp_path, monkeypatch):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{upstream.server_address[1]}"
    _Upstream.refs.clear()
    calls = []

    def fake_resolve(page_url, cfg=None, progress=None, use_cache=True):
        calls.append((page_url, use_cache))
        return {
            "ok": True,
            "items": [
                {
                    "kind": "video",
                    "url": base + "/good",
                    "referer": "https://m.douyin.com/",
                    "name": "新的视频.mp4",
                }
            ],
        }

    monkeypatch.setattr(engines, "resolve_douyin", fake_resolve)
    port = _start_server(tmp_path, monkeypatch)
    try:
        response = _post_download(port, base + "/bad", PAGE)
        assert response.status == 206
        data = response.read()
        assert data[4:8] == b"ftyp"
        assert calls == [(PAGE, False)]
        assert _Upstream.refs[0] == ("/bad", "https://www.douyin.com/")
        assert _Upstream.refs[-1] == ("/good", "https://m.douyin.com/")
    finally:
        server.stop()
        upstream.shutdown()
        upstream.server_close()


def test_download_still_expired_returns_chinese_error(tmp_path, monkeypatch):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
    upstream.daemon_threads = True
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{upstream.server_address[1]}"

    def fake_resolve(page_url, cfg=None, progress=None, use_cache=True):
        return {"ok": True, "items": [{"kind": "video", "url": base + "/bad"}]}

    monkeypatch.setattr(engines, "resolve_douyin", fake_resolve)
    port = _start_server(tmp_path, monkeypatch)
    try:
        response = _post_download(port, base + "/bad", PAGE)
        assert response.status == 200
        payload = json.loads(response.read().decode("utf-8"))
        assert payload == {"ok": False, "error": "抖音直链已过期，请重新解析"}
    finally:
        server.stop()
        upstream.shutdown()
        upstream.server_close()
