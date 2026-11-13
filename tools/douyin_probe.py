#!/usr/bin/env python
"""抖音解析分层探测：直连 → yt-dlp → 第三方接口。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe tools\\douyin_probe.py "https://v.douyin.com/xxxx/"
    .venv\\Scripts\\python.exe tools\\douyin_probe.py          # 用内置示例短链

只打印每层结果，不抛异常栈；全部失败时退出码 2。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))

import webgrab
import webgrab.config as config
import webgrab.engines as engines
import webgrab.media as media


DEFAULT_URL = "https://v.douyin.com/iRNBho6u/"


def show(label: str, func) -> bool:
    print(f"\n--- {label} ---")
    try:
        result = func()
    except engines.EngineError as exc:
        print(f"失败：{exc}")
        return False
    except Exception as exc:  # 网络/TLS 抖动等，只打印首行
        print(f"异常：{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}")
        return False
    if not result:
        print("无数据")
        return False
    title = result.get("title") or ""
    items = result.get("items") or []
    print(f"标题：{title or '(空)'}")
    print(f"媒体：{len(items)} 项")
    for index, item in enumerate(items[:5], 1):
        print(f"  {index}. [{item.get('kind')}] {item.get('url')}")
    if len(items) > 5:
        print(f"  ... 还有 {len(items) - 5} 项")
    if result.get("note"):
        print(f"备注：{result['note']}")
    return bool(items)


def main() -> int:
    url = (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL).strip()
    print(f"WebGrab {webgrab.__version__} 抖音探测")
    print(f"输入：{url}")
    print(f"ffmpeg：{media.ffmpeg_available()}  ({media.ffmpeg_dir() or '未找到'})")
    print(f"平台判定：{engines.is_platform(url) or '未知'}")

    results = []
    results.append(show("第 1 层 直连解析", lambda: engines.douyin_direct(url)))
    results.append(show("第 2 层 yt-dlp", lambda: engines.ytdlp_resolve(url)))

    apis = config.douyin_apis()
    print(f"\n--- 第 3 层 第三方接口（{len(apis)} 条）---")
    for index, api in enumerate(apis, 1):
        print(f"[{index}] {api}")

        def call(template=api):
            return engines.douyin_api(template, url)

        results.append(show(f"接口 {index}", call))

    print("\n--- 完整链路 resolve_douyin ---")
    try:
        final = engines.resolve_douyin(url, None, progress=lambda stage, msg: print(f"  [{stage}] {msg}"))
        print(f"结果：{final.get('title')} / {len(final.get('items') or [])} 项")
        success = True
    except engines.EngineError as exc:
        print(f"结果：失败 -> {exc}")
        success = False
    except Exception:
        print("结果：失败 -> 未知异常")
        traceback.print_exc()
        success = False

    print("\n结论：" + ("至少一层可用" if any(results) or success else "全部层失败（链路仍可配置）"))
    return 0 if (any(results) or success) else 2


if __name__ == "__main__":
    sys.exit(main())
