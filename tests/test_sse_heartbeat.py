"""SSE 心跳：扫描长时间没有事件时，服务端每 3s 发 ping，客户端才不会 8s 回退。"""

from __future__ import annotations

import http.client
import json
import time

from webgrab import extract, server


def _start(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "scan_page", _fake_scan)
    return server.start(str(tmp_path / "static"), 0)


def _fake_scan(url, allow_third_party=False, progress=None):
    if progress:
        progress("open", "打开页面")
        progress("probe", "找到 3 个")
    time.sleep(7.0)
    return {"ok": True, "url": url, "title": "t", "items": [], "groups": {"image": [], "video": []}}


def test_stream_sends_heartbeat_while_scan_is_slow(tmp_path, monkeypatch):
    port = _start(tmp_path, monkeypatch)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=12)
        conn.request("GET", "/api/scan_stream?url=http%3A%2F%2Fexample.com%2Fslow")
        resp = conn.getresponse()
        assert resp.status == 200
        assert "text/event-stream" in (resp.getheader("Content-Type") or "")

        started = time.time()
        pings = 0
        done = None
        while time.time() - started < 11:
            line = resp.fp.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text.startswith("data:"):
                continue
            payload = json.loads(text[5:].strip())
            if payload.get("stage") == "ping":
                pings += 1
            if payload.get("stage") == "done":
                done = payload
                break
        conn.close()

        assert pings >= 2, f"7s 扫描期间应至少 2 次心跳，实际 {pings}"
        assert done is not None and done.get("ok") is True
        assert pings >= 1 and done is not None
    finally:
        server.stop()


def test_stream_keeps_done_error_code(tmp_path, monkeypatch):
    class Boom(extract.ScanError):
        pass

    def fail(url, allow_third_party=False, progress=None):
        raise Boom("需要验证", code="NEEDS_VERIFICATION")

    monkeypatch.setattr(extract, "scan_page", fail)
    port = server.start(str(tmp_path / "static"), 0)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/scan_stream?url=http%3A%2F%2Fexample.com%2Fx")
        resp = conn.getresponse()
        done = None
        for _ in range(50):
            line = resp.fp.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if text.startswith("data:"):
                payload = json.loads(text[5:].strip())
                if payload.get("stage") == "done":
                    done = payload
                    break
        conn.close()
        assert done is not None
        assert done.get("ok") is False
        assert done.get("error_code") == "NEEDS_VERIFICATION"
    finally:
        server.stop()
