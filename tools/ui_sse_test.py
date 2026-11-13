"""前端 SSE 回退去重检查（Playwright + 本机 Edge）。

验证：
1. EventSource 报错时，先 close 旧流，再发唯一一次 POST /api/scan；
2. 旧流的第二次 onerror 不会产生第二个 POST；
3. 同一轮只创建一个 EventSource。

用法：
    .venv\\Scripts\\python.exe tools\\ui_sse_test.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / ".tmp" / "pw-sse"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = str(TMP)
sys.path.insert(0, str(ROOT / "core"))

from playwright.sync_api import sync_playwright  # noqa: E402

from webgrab import server  # noqa: E402

FAKE = """
() => {
  const realFetch = window.fetch.bind(window);
  window.__sse = { opened: 0, closed: 0, posts: 0, order: [], instances: [] };
  class FakeES {
    constructor(url) {
      this.url = url;
      this.closed = false;
      window.__sse.opened++;
      window.__sse.instances.push(this);
      setTimeout(() => { if (this.onerror) this.onerror(new Event('error')); }, 120);
    }
    close() {
      if (this.closed) return;
      this.closed = true;
      window.__sse.closed++;
      window.__sse.order.push('close');
    }
  }
  window.EventSource = FakeES;
  window.fetch = function (u, o) {
    if (String(u).indexOf('/api/scan') === 0) {
      window.__sse.posts++;
      window.__sse.order.push('fetch');
      return new Promise(() => {});
    }
    return realFetch(u, o);
  };
}
"""


def main() -> int:
    port = server.start(str(ROOT / "core" / "webgrab" / "static"), 0)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 393, "height": 852})
            page.goto(base + "/", wait_until="load")
            page.wait_for_selector("#result:not(.hidden)", timeout=15_000)

            page.evaluate(FAKE)
            page.fill("#urlInput", "https://www.douyin.com/video/7412345678901234567")
            page.click("#goBtn")
            page.wait_for_timeout(1_200)

            first = page.evaluate("window.__sse")
            assert first["opened"] == 1, first
            assert first["closed"] == 1, first
            assert first["posts"] == 1, first
            assert first["order"] == ["close", "fetch"], first

            # 旧流再次报错：不应再发 POST
            page.evaluate("window.__sse.instances[0].onerror && window.__sse.instances[0].onerror(new Event('error'))")
            page.wait_for_timeout(500)
            second = page.evaluate("window.__sse")
            assert second["posts"] == 1, second

            print("SSE 去重：ok")
            print(f"  EventSource 打开 {first['opened']} 次，close {first['closed']} 次")
            print(f"  POST /api/scan {second['posts']} 次，顺序 {second['order']}")
            browser.close()
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
