"""Android（Chaquopy）入口：供 GrabService 调用。

静态文件由 Android 壳复制到应用目录后通过 ``WEBGRAB_STATIC_DIR`` 环境变量
或 ``start(static_dir=...)`` 传入；未设置时按包内 static 目录查找。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from . import server


def _default_static() -> Path:
    env = os.environ.get("WEBGRAB_STATIC_DIR", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "static"


def start(port: int = 8765, static_dir=None) -> int:
    """启动本地服务，返回实际端口；不阻塞、不打开浏览器。"""
    root = Path(static_dir) if static_dir else _default_static()
    return server.start(root, int(port))


def stop() -> None:
    server.stop()


def run(port: int = 8765, static_dir=None) -> None:
    """前台运行（调试/独立进程用）。"""
    actual = start(port, static_dir)
    print(f"WEBGRAB_URL=http://127.0.0.1:{actual}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stop()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
