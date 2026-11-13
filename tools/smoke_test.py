#!/usr/bin/env python
"""WebGrab 端到端冒烟测试（不依赖外网）。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe tools\\smoke_test.py

覆盖：health / config / scan / SSE / 下载 Range / 代理 Range / 静态 Range /
路径穿越 / share / /demo（素材就绪时）/ 转音频（ffmpeg 就绪时）。
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core"
STATIC = CORE / "webgrab" / "static"
sys.path.insert(0, str(CORE))

# 数据目录放到临时位置，别污染本机 ~/.webgrab
_TMP_HOME = tempfile.mkdtemp(prefix="webgrab-smoke-home-")
os.environ["WEBGRAB_HOME"] = _TMP_HOME

PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def ok(name: str, detail: str = "") -> None:
    PASS.append(name)
    print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))


def bad(name: str, detail: str) -> None:
    FAIL.append(f"{name}: {detail}")
    print(f"  FAIL  {name}  -> {detail}")


def skip(name: str, detail: str) -> None:
    SKIP.append(f"{name}: {detail}")
    print(f"  SKIP  {name}  ({detail})")


def check(name: str, cond: bool, detail: str = "") -> bool:
    if cond:
        ok(name, detail)
    else:
        bad(name, detail or "断言不成立")
    return cond


# --------------------------------------------------------------- 本地素材服务


def tiny_png(width: int = 3, height: int = 2) -> bytes:
    raw = b"".join(b"\x00" + bytes([220, 40, 40, 255]) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PNG = tiny_png()
CLIP = (b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40)  # 假 mp4，只测字节流
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>冒烟测试页</title>
<meta property="og:image" content="/pic.png"></head><body>
<h1>冒烟测试</h1>
<img src="/pic.png" alt="图">
<video src="/clip.mp4" poster="/pic.png" controls></video>
<a href="/clip.mp4">下载视频</a>
</body></html>""".encode("utf-8")


class Fixture(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    FILES: dict[str, tuple[bytes, str]] = {}

    def log_message(self, *_args):
        pass

    def _body(self):
        clean = self.path.split("?", 1)[0]
        if clean in self.FILES:
            return self.FILES[clean]
        if self.path.startswith("/page"):
            return PAGE, "text/html; charset=utf-8"
        if self.path.startswith("/pic"):
            return PNG, "image/png"
        if self.path.startswith("/clip"):
            return CLIP, "video/mp4"
        return b"not found", "text/plain; charset=utf-8"

    def do_HEAD(self):
        self._send(False)

    def do_GET(self):
        self._send(True)

    def _send(self, body: bool):
        data, ctype = self._body()
        status = 200
        start, end = 0, len(data) - 1
        rng = self.headers.get("Range") or ""
        if rng.startswith("bytes=") and "," not in rng:
            spec = rng[6:].strip()
            try:
                a, b = spec.split("-", 1)
                if a:
                    start = int(a)
                    end = int(b) if b else len(data) - 1
                else:
                    start = max(0, len(data) - int(b))
                if start >= len(data) or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                end = min(end, len(data) - 1)
                status = 206
            except (ValueError, TypeError):
                status = 200
        payload = data[start : end + 1] if status == 206 else data
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.end_headers()
        if body:
            self.wfile.write(payload)


# ------------------------------------------------------------------ HTTP 工具


def request(port: int, method: str, path: str, body: bytes | None = None,
            headers: dict | None = None, timeout: float = 30.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, dict((k.lower(), v) for k, v in resp.getheaders()), data, resp
    finally:
        conn.close()


def get_json(port: int, path: str):
    status, _headers, data, _ = request(port, "GET", path)
    return status, json.loads(data.decode("utf-8"))


def post_json(port: int, path: str, payload: dict):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    status, _headers, data, _ = request(
        port, "POST", path, raw, {"Content-Type": "application/json; charset=utf-8"}
    )
    return status, json.loads(data.decode("utf-8"))


def read_sse(port: int, path: str, body: bytes | None = None,
             headers: dict | None = None) -> tuple[list[dict], dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request("GET" if body is None else "POST", path, body=body, headers=headers or {})
        resp = conn.getresponse()
        ctype = resp.getheader("Content-Type") or ""
        events: list[dict] = []
        while True:
            line = resp.fp.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if text.startswith("data:"):
                try:
                    events.append(json.loads(text[5:].strip()))
                except ValueError:
                    pass
            if events and events[-1].get("stage") == "done":
                break
        return events, {"status": resp.status, "ctype": ctype}
    finally:
        conn.close()


# ---------------------------------------------------------------------- 开始


def main() -> int:
    import webgrab
    import webgrab.media as media
    import webgrab.server as server

    print(f"WebGrab {webgrab.__version__} 冒烟测试")
    print(f"数据目录: {os.environ['WEBGRAB_HOME']}")
    print(f"ffmpeg: {media.ffmpeg_available()}  ({media.ffmpeg_dir()})")

    # 本地素材站
    class _QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):  # 客户端主动断开属正常
            pass

    fixture = _QuietServer(("127.0.0.1", 0), Fixture)
    fixture.daemon_threads = True
    fixture_port = fixture.server_address[1]
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{fixture_port}"
    print(f"素材站: {base_url}")

    port = server.start(str(STATIC), 8765)
    print(f"WebGrab 服务: http://127.0.0.1:{port}\n")
    try:
        # 1. health
        print("[1] /api/health")
        status, health = get_json(port, "/api/health")
        check("health 200", status == 200, f"status={status}")
        check("health ok", health.get("ok") is True)
        check("health version", health.get("version") == "1.0.0")
        check("health platform", health.get("platform") == "windows")
        check("health port", health.get("port") == port, f"port={health.get('port')}")
        check("health ffmpeg 字段", isinstance(health.get("ffmpeg"), bool))
        check("health convert 字段", isinstance(health.get("convert"), bool))

        # 2. Host 白名单
        print("[2] Host 校验")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/health", headers={"Host": "evil.example.com"})
        r = conn.getresponse()
        r.read()
        check("Host 非本机 403", r.status == 403, f"status={r.status}")
        conn.close()

        # 3. config
        print("[3] /api/config")
        status, cfg = get_json(port, "/api/config")
        originals = list(cfg.get("douyin_apis") or [])
        check("config GET ok", status == 200 and cfg.get("ok") is True)
        check("config 默认接口非空", len(originals) >= 1)
        _s, bad_cfg = post_json(port, "/api/config", {"douyin_apis": ["ftp://x/{url}"]})
        check("config 非法以 ok:false 返回", bad_cfg.get("ok") is False and "http" in bad_cfg.get("error", ""))
        _s, good_cfg = post_json(
            port, "/api/config",
            {"douyin_apis": ["https://a.example/api?url={url}", "https://b.example/x/{url}"]},
        )
        check("config 保存成功", good_cfg.get("ok") is True and len(good_cfg.get("douyin_apis") or []) == 2)
        status, cfg2 = get_json(port, "/api/config")
        check("config 落盘后可读", status == 200 and cfg2.get("douyin_apis") == good_cfg.get("douyin_apis"))
        post_json(port, "/api/config", {"douyin_apis": originals})

        # 4. scan
        print("[4] /api/scan")
        status, scan = post_json(
            port, "/api/scan", {"url": f"{base_url}/page.html", "allow_third_party": False}
        )
        check("scan 200", status == 200)
        check("scan ok", scan.get("ok") is True, json.dumps(scan, ensure_ascii=False)[:160])
        items = scan.get("items") or []
        kinds = [i.get("kind") for i in items]
        check("scan 有图片", "image" in kinds, f"kinds={kinds}")
        check("scan 有视频", "video" in kinds, f"kinds={kinds}")
        check("scan 绝对 URL", all(str(i.get("url", "")).startswith("http") for i in items))
        check("scan 有 referer", all("referer" in i for i in items))
        check("scan id 稳定 i1..", [i.get("id") for i in items] == [f"i{n}" for n in range(1, len(items) + 1)])
        img = next((i for i in items if i.get("kind") == "image"), {})
        check("scan 图片宽高", (img.get("w"), img.get("h")) == (3, 2), f"{img.get('w')}x{img.get('h')}")
        check("scan engine=direct", scan.get("engine") == "direct", str(scan.get("engine")))
        groups = scan.get("groups") or {}
        check("scan groups 齐全", isinstance(groups.get("image"), list) and isinstance(groups.get("video"), list))
        _s, empty = post_json(port, "/api/scan", {"url": f"{base_url}/none.html"})
        check("scan 空页中文错误", empty.get("ok") is False and "没有" in empty.get("error", ""))

        # 5. SSE（GET + POST）
        print("[5] /api/scan_stream")
        events, meta = read_sse(
            port, "/api/scan_stream?url=" + quote(f"{base_url}/page.html", safe="") + "&tp=0"
        )
        check("SSE Content-Type", "text/event-stream" in meta["ctype"], meta["ctype"])
        stages = [e.get("stage") for e in events]
        check("SSE 阶段 open", "open" in stages, str(stages))
        check("SSE 阶段 probe", "probe" in stages, str(stages))
        check("SSE 阶段 done", "done" in stages, str(stages))
        check("SSE done ok", bool(events) and events[-1].get("ok") is True)
        check("SSE done result", bool(events) and isinstance(events[-1].get("result"), dict))
        probe_ev = next((e for e in events if e.get("stage") == "probe"), {})
        check("SSE probe found", isinstance(probe_ev.get("found"), int) and probe_ev["found"] >= 1)
        body = json.dumps({"url": f"{base_url}/page.html", "allow_third_party": False}).encode()
        events2, _ = read_sse(port, "/api/scan_stream", body, {"Content-Type": "application/json"})
        check("SSE POST done", bool(events2) and events2[-1].get("stage") == "done" and events2[-1].get("ok") is True)

        # 6. 下载 Range
        print("[6] /api/dl")
        dl = f"/api/dl?u={quote(base_url + '/clip.mp4', safe='')}&name={quote('片段 1.mp4')}"
        status, headers, data, _ = request(port, "GET", dl, headers={"Range": "bytes=0-99"})
        check("dl Range 206", status == 206, f"status={status}")
        check("dl Content-Range", headers.get("content-range") == f"bytes 0-99/{len(CLIP)}", headers.get("content-range"))
        check("dl 长度 100", len(data) == 100, f"len={len(data)}")
        check("dl attachment", "attachment" in headers.get("content-disposition", ""))
        check("dl UTF-8 文件名", "filename*=UTF-8''" in headers.get("content-disposition", ""), headers.get("content-disposition"))
        status, headers, data, _ = request(port, "GET", dl)
        check("dl 全量 200", status == 200 and data == CLIP, f"status={status} len={len(data)}")

        # 7. 代理 Range
        print("[7] /api/proxy")
        px = f"/api/proxy?u={quote(base_url + '/clip.mp4', safe='')}"
        status, headers, data, _ = request(port, "GET", px, headers={"Range": "bytes=10-19"})
        check("proxy Range 206", status == 206, f"status={status}")
        check("proxy Content-Range", headers.get("content-range") == f"bytes 10-19/{len(CLIP)}", headers.get("content-range"))
        check("proxy 无 attachment", "content-disposition" not in headers)
        check("proxy 10 字节", len(data) == 10 and data == CLIP[10:20])

        # 8. 静态 & Range
        print("[8] 静态文件")
        status, headers, data, _ = request(port, "GET", "/")
        check("GET / 200", status == 200, f"status={status}")
        check("/ 是前端", b"urlInput" in data and b"app.js" in data)
        status, headers, data, _ = request(port, "GET", "/static/index.html", headers={"Range": "bytes=0-49"})
        check("静态 Range 206", status == 206, f"status={status}")
        check("静态 Content-Range", (headers.get("content-range") or "").startswith("bytes 0-49/"), headers.get("content-range"))
        check("静态 Range 长度", len(data) == 50, f"len={len(data)}")
        check("html no-cache", "no-cache" in (headers.get("cache-control") or ""), headers.get("cache-control"))
        status, _h, data, _ = request(port, "GET", "/share?url=" + quote("https://example.com/x"))
        check("GET /share 200", status == 200 and b"urlInput" in data)
        status, _h, data, _ = request(port, "GET", "/static/../webgrab/server.py")
        check("路径穿越被拦", status == 404, f"status={status}")
        status, _h, data, _ = request(port, "GET", "/api/does-not-exist")
        check("未知 API 404 JSON", status == 404 and json.loads(data.decode()).get("ok") is False)

        # 9. /demo（等 frontend-ui 素材）
        print("[9] /demo")
        if (STATIC / "demo.html").is_file():
            status, _h, data, _ = request(port, "GET", "/demo")
            check("/demo 200", status == 200 and b"demo" in data.lower())
            status, demo = post_json(port, "/api/scan", {"url": f"http://127.0.0.1:{port}/demo"})
            check("/demo 扫描 5 项", demo.get("ok") is True and len(demo.get("items") or []) == 5,
                  json.dumps(demo, ensure_ascii=False)[:200])
            demo_img = next((i for i in demo.get("items") or [] if i.get("kind") == "image"), {})
            check("/demo 图片宽高", (demo_img.get("w") or 0) > 0 and (demo_img.get("h") or 0) > 0,
                  f"{demo_img.get('w')}x{demo_img.get('h')}")
        else:
            skip("/demo", "static/demo.html 尚未由 frontend-ui 交付")

        # 10. 转音频
        print("[10] /api/convert/upload")
        if media.ffmpeg_available():
            wav_path = Path(_TMP_HOME) / "tone.wav"
            with wave.open(str(wav_path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(22050)
                w.writeframes(b"".join(
                    struct.pack("<h", int(8000 * __import__("math").sin(2 * 3.14159 * 440 * n / 22050)))
                    for n in range(22050)
                ))
            raw = wav_path.read_bytes()
            for fmt in ("mp3", "m4a", "wav"):
                status, headers, data, _ = request(
                    port, "POST", f"/api/convert/upload?fmt={fmt}", raw,
                    {"Content-Type": "application/octet-stream",
                     "X-Filename": quote("测试 音频.wav")},
                )
                payload = json.loads(data.decode())
                if not check(f"convert {fmt} ok", status == 200 and payload.get("ok") is True,
                             json.dumps(payload, ensure_ascii=False)[:160]):
                    continue
                check(f"convert {fmt} 名字", str(payload.get("name", "")).endswith("." + fmt), str(payload.get("name")))
                status, headers, data, _ = request(port, "GET", payload["url"])
                check(f"convert {fmt} 可下载", status == 200 and len(data) > 0)
                check(f"convert {fmt} attachment", "attachment" in headers.get("content-disposition", ""))
            status, _h, data, _ = request(
                port, "POST", "/api/convert/upload?fmt=mp3", b"", {"X-Filename": quote("空.wav")}
            )
            payload = json.loads(data.decode())
            check("convert 空文件中文错误", payload.get("ok") is False and "没有" in payload.get("error", ""))
            status, _h, data, _ = request(port, "GET", "/api/convert/file?id=c99999")
            check("convert 过期中文错误", json.loads(data.decode()).get("ok") is False)
        else:
            skip("convert", "缺少 ffmpeg，无法转音频")

        # 11. HLS / yt / 转链接（需要 ffmpeg 现场生成素材）
        print("[11] HLS / yt / convert/link")
        if media.ffmpeg_available():
            gen = Path(_TMP_HOME) / "gen"
            gen.mkdir(parents=True, exist_ok=True)
            ffmpeg = media._tool("ffmpeg")
            try:
                subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                     "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                     "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                     "-shortest", str(gen / "test.mp4")],
                    check=True, capture_output=True, timeout=90,
                )
                subprocess.run(
                    [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                     "-i", str(gen / "test.mp4"), "-c", "copy", "-f", "hls",
                     "-hls_time", "1", "-hls_list_size", "0", str(gen / "playlist.m3u8")],
                    check=True, capture_output=True, timeout=90,
                )
            except Exception as exc:
                skip("HLS/yt", f"现场生成测试素材失败：{exc}")
            else:
                for item in gen.iterdir():
                    ctype = {
                        ".m3u8": "application/vnd.apple.mpegurl",
                        ".ts": "video/mp2t",
                    }.get(item.suffix, "video/mp4")
                    if item.is_file():
                        Fixture.FILES["/gen/" + item.name] = (item.read_bytes(), ctype)
                status, hls_headers, data, _ = request(
                    port, "GET",
                    "/api/hls?u=" + quote(base_url + "/gen/playlist.m3u8", safe="")
                    + "&name=" + quote("测 HLS.mp4"),
                    timeout=120,
                )
                check("HLS 转 mp4", status == 200 and data[4:8] == b"ftyp", f"status={status} len={len(data)}")
                check("HLS attachment", "attachment" in hls_headers.get("content-disposition", ""))
                status, _h, data, _ = request(
                    port, "GET",
                    "/api/yt?u=" + quote(base_url + "/gen/test.mp4", safe="") + "&name=" + quote("测 YT.mp4"),
                    timeout=180,
                )
                check("yt 下载 mp4", status == 200 and data[4:8] == b"ftyp", f"status={status} len={len(data)}")
                status, payload = post_json(
                    port, "/api/convert/link",
                    {"u": base_url + "/gen/test.mp4", "via": "direct", "fmt": "mp3", "name": "链接 音频.mp4"},
                )
                check("convert/link ok", status == 200 and payload.get("ok") is True,
                      json.dumps(payload, ensure_ascii=False)[:160])
                if payload.get("ok"):
                    status, headers, data, _ = request(port, "GET", payload["url"])
                    check("convert/link 可下载", status == 200 and len(data) > 0)
        else:
            skip("HLS/yt", "缺少 ffmpeg")

        # 12. 断连不崩
        print("[12] 客户端断开")
        s = __import__("socket").create_connection(("127.0.0.1", port))
        s.sendall(b"GET /api/scan_stream?url=" + quote(base_url + "/page.html", safe="").encode() + b" HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        time.sleep(0.2)
        s.close()
        time.sleep(0.3)
        status, health2 = get_json(port, "/api/health")
        check("断连后服务仍活", status == 200 and health2.get("ok") is True)
    finally:
        server.stop()
        fixture.shutdown()
        fixture.server_close()
        shutil.rmtree(_TMP_HOME, ignore_errors=True)

    print("\n结果：%d 通过，%d 失败，%d 跳过" % (len(PASS), len(FAIL), len(SKIP)))
    if SKIP:
        print("跳过项：")
        for item in SKIP:
            print("  -", item)
    if FAIL:
        print("失败项：")
        for item in FAIL:
            print("  -", item)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
