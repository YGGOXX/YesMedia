"""纯函数单元测试：提取、过滤、命名、配置、平台识别、静态 Range。

不访问外网；不依赖 ffmpeg。
"""

from __future__ import annotations

import json
import os
import struct
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import pytest

from webgrab import config, engines, extract, net


def test_extract_media_basic():
    html = """
    <html><head>
      <title> 测试 &amp; 页面 </title>
      <meta property="og:image" content="https://cdn.example.com/cover.jpg">
      <meta name="twitter:image" content="/tw.png">
      <link rel="image_src" href="/link.png">
    </head><body>
      <img src="/a.jpg">
      <img data-src="/lazy.jpg">
      <img data-original="https://img.example.com/orig.png">
      <img srcset="/small.jpg 320w, /large.jpg 1200w">
      <picture><source srcset="/pic.webp 800w"><img src="/fallback.jpg"></picture>
      <div style="background-image:url('/bg.jpg')"></div>
      <video src="/v.mp4" poster="/poster.jpg"><source src="/v2.webm"></video>
      <a href="/doc.pdf">pdf</a>
      <a href="/direct.m3u8">hls</a>
      <a href="/clip.MP4?x=1">mp4</a>
      <img src="data:image/png;base64,AAAA">
      <img src="/favicon.ico">
      <img src="/logo.png">
      <script>var x="https://cdn.example.com/live.m3u8?token=1";</script>
    </body></html>
    """
    items = extract.extract_media(html, "https://site.example.com/page")
    urls = [i["url"] for i in items]
    assert "https://site.example.com/a.jpg" in urls
    assert "https://site.example.com/lazy.jpg" in urls
    assert "https://img.example.com/orig.png" in urls
    assert "https://site.example.com/large.jpg" in urls  # srcset 取最大
    assert "https://site.example.com/pic.webp" in urls
    assert "https://site.example.com/bg.jpg" in urls
    assert "https://site.example.com/v.mp4" in urls
    assert "https://site.example.com/v2.webm" in urls
    assert "https://site.example.com/direct.m3u8" in urls
    assert "https://site.example.com/clip.MP4?x=1" in urls
    assert "https://cdn.example.com/live.m3u8?token=1" in urls
    assert "https://cdn.example.com/cover.jpg" in urls
    assert "https://site.example.com/tw.png" in urls
    assert "https://site.example.com/link.png" in urls
    assert not any("data:" in u for u in urls)
    assert not any("favicon" in u or "logo" in u for u in urls)
    assert not any(u.endswith(".pdf") for u in urls)
    # 去重
    assert len(urls) == len(set(urls))


def test_extract_kinds_and_thumbs():
    html = '<video src="/v.mp4" poster="/p.jpg"></video><img src="/i.png">'
    items = extract.extract_media(html, "https://x.test/")
    video = next(i for i in items if i["url"].endswith("v.mp4"))
    image = next(i for i in items if i["url"].endswith("i.png"))
    assert video["kind"] == "video" and video["thumb"] == "https://x.test/p.jpg"
    assert image["kind"] == "image"


def test_filter_candidates_third_party():
    cands = [
        {"url": "https://other.test/a.jpg", "kind": "image"},
        {"url": "https://other.test/b.mp4", "kind": "video"},
        {"url": "https://site.test/c.mp4", "kind": "video"},
    ]
    kept = extract.filter_candidates(cands, "https://site.test/page", False)
    assert [c["url"] for c in kept] == ["https://other.test/a.jpg", "https://site.test/c.mp4"]
    kept2 = extract.filter_candidates(cands, "https://site.test/page", True)
    assert len(kept2) == 3


def test_page_title():
    assert extract.page_title("<html><head><title> 你好 世界 </title></head></html>") == "你好 世界"
    assert extract.page_title('<meta property="og:title" content="OG 标题"><title>T</title>') == "OG 标题"
    assert extract.page_title("<h1>只有 H1</h1>") == "只有 H1"
    assert extract.page_title("", "兜底") == "兜底"


def test_decode_html_gbk():
    raw = '<html><head><meta charset="gbk"><title>中文标题</title></head></html>'.encode("gbk")
    assert "中文标题" in extract._decode_html(raw)


def test_net_filename_and_clean():
    stem, ext = net.filename_from("https://x.test/a/b/photo.JPG?x=1", "image/jpeg")
    assert (stem, ext) == ("photo", "jpg")
    stem, ext = net.filename_from("https://x.test/download?id=9", "video/mp4")
    assert ext == "mp4" and stem
    stem, ext = net.filename_from("https://x.test/noext", "", "fallback")
    assert (stem, ext) == ("noext", "")
    stem, ext = net.filename_from("https://x.test/", "", "fallback")
    assert stem == "fallback"
    assert net.clean_filename('a/b\\c:d*e?f"g<h>i|j\r\nk') == "a_b_c_d_e_f_g_h_i_j__k"
    assert len(net.clean_filename("名" * 200)) <= 80
    assert net.human_size(0)
    assert "MB" in net.human_size(5 * 1024 * 1024)


def test_image_size_png():
    raw = b"".join(b"\x00" + bytes([1, 2, 3, 255]) * 3 for _ in range(2))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 3, 2, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    assert net.image_size(png) == (3, 2)
    assert net.image_size(b"not an image") == (0, 0)


@pytest.mark.parametrize("apis,valid", [
    (["https://a.test/x?url={url}"], True),
    (["http://a.test/{url}"], True),
    (["https://a.test/"], False),
    (["ftp://a.test/{url}"], False),
    ([], False),
    (["https://a.test/{url}"] * 6, False),
    ("https://a.test/{url}", False),
])
def test_config_validate(apis, valid):
    assert (config.validate_douyin_apis(apis) == "") is valid


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBGRAB_HOME", str(tmp_path))
    config.reset_cache()
    assert config.data_dir() == tmp_path
    assert config.douyin_apis()
    saved = config.save_config(["https://api.test/dy?url={url}"])
    assert saved["douyin_apis"] == ["https://api.test/dy?url={url}"]
    assert json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))["douyin_apis"] == saved["douyin_apis"]
    with pytest.raises(ValueError):
        config.save_config(["bad"])


def test_engines_platform():
    assert engines.is_douyin("https://v.douyin.com/abc/")
    assert engines.is_douyin("复制打开抖音 https://www.douyin.com/video/123")
    assert engines.is_platform("https://www.bilibili.com/video/BV1xx") == "bilibili"
    assert engines.is_platform("https://b23.tv/xyz") == "bilibili"
    assert engines.is_platform("https://weibo.com/tv/show/1") == "weibo"
    assert engines.is_platform("https://youtu.be/xyz") == "youtube"
    assert engines.is_platform("https://example.com/x") == ""
    assert engines.is_platform("https://www.iesdouyin.com/share/video/123/") == "douyin"


def test_ytdlp_bad_media_ext():
    assert not engines._looks_media({"url": "https://x.test/page.html", "ext": "html"})
    assert not engines._looks_media({"url": "https://x.test/page", "ext": "unknown_video"})
    assert engines._looks_media({"url": "https://x.test/a.mp4", "ext": "mp4"})


class _StaticHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):
        pass

    def do_GET(self):
        body = b"0123456789" * 10
        status, start, end = 200, 0, len(body) - 1
        rng = self.headers.get("Range") or ""
        if rng.startswith("bytes="):
            spec = rng[6:]
            a, b = spec.split("-", 1)
            start = int(a) if a else max(0, len(body) - int(b))
            end = int(b) if b else len(body) - 1
            status = 206
        payload = body[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        self.end_headers()
        self.wfile.write(payload)


def test_server_static_range_and_host(tmp_path, monkeypatch):
    import http.client
    from webgrab import server

    monkeypatch.setenv("WEBGRAB_HOME", str(tmp_path / "home"))
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    (tmp_path / "static" / "app.js").write_text("console.log(1)", encoding="utf-8")
    port = server.start(str(tmp_path / "static"), 8788)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/static/app.js", headers={"Range": "bytes=0-6"})
        resp = conn.getresponse()
        data = resp.read()
        assert resp.status == 206
        assert data == b"console"
        assert resp.getheader("Content-Range") == "bytes 0-6/14"
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/", headers={"Host": "evil.test"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 403
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/health")
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode())
        assert payload["ok"] and payload["port"] == port
        conn.close()
    finally:
        server.stop()
