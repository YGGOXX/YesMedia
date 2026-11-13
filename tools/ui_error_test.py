"""错误码 UI 检查：服务端返回 NEEDS_VERIFICATION 时，错误卡应给出原因和动作。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / ".tmp" / "pw-err"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = str(TMP)
sys.path.insert(0, str(ROOT / "core"))

from playwright.sync_api import sync_playwright  # noqa: E402

from webgrab import extract, server  # noqa: E402


def boom(url, allow_third_party=False, progress=None):
    raise extract.ScanError("抖音要求安全验证", code="NEEDS_VERIFICATION")


def main() -> int:
    extract.scan_page = boom
    port = server.start(str(ROOT / "core" / "webgrab" / "static"), 0)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 393, "height": 852})
            page.goto(base + "/", wait_until="load")
            page.wait_for_selector("#error:not(.hidden)", timeout=15_000)
            text = page.inner_text("#errText")
            action = page.inner_text("#retryBtn")
            assert "安全验证" in text, text
            assert action == "复制链接", action
            print("错误码 UI：ok")
            print(f"  errText={text}")
            print(f"  action={action}")
            browser.close()
        return 0
    finally:
        server.stop()


if __name__ == "__main__":
    raise SystemExit(main())
