"""检查 core/webgrab 与 Android 生成副本是否一致（构建前必跑）。

对应副本：
  core/webgrab/**/*.py            -> android/app/src/main/python/webgrab/**/*.py
  core/webgrab/static/**          -> android/app/src/main/assets/webgrab-static/**
  core/webgrab/static/**          -> android/app/src/main/python/webgrab/static/**

用法：
    .venv\\Scripts\\python.exe tools\\check_python_sync.py
退出码 0=一致，1=有差异（打印明细），2=路径缺失。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "core" / "webgrab"
ANDROID = ROOT / "android"
PY_DST = ANDROID / "app" / "src" / "main" / "python" / "webgrab"
ASSETS_DST = ANDROID / "app" / "src" / "main" / "assets" / "webgrab-static"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(base: Path, suffixes: tuple[str, ...] = ()) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        out[path.relative_to(base).as_posix()] = digest(path)
    return out


def compare(label: str, src: dict[str, str], dst: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for name in sorted(src.keys() - dst.keys()):
        problems.append(f"{label}: 缺少 {name}")
    for name in sorted(dst.keys() - src.keys()):
        problems.append(f"{label}: 多出 {name}")
    for name in sorted(src.keys() & dst.keys()):
        if src[name] != dst[name]:
            problems.append(f"{label}: 内容不同 {name}")
    return problems


def main() -> int:
    if not CORE.is_dir() or not PY_DST.is_dir():
        print(f"路径不存在：{CORE} 或 {PY_DST}", file=sys.stderr)
        return 2

    py_src = snapshot(CORE, (".py",))
    py_dst = snapshot(PY_DST, (".py",))
    static_src = snapshot(CORE / "static")
    assets_dst = snapshot(ASSETS_DST)
    py_static_dst = snapshot(PY_DST / "static")

    problems = []
    problems += compare("python", py_src, py_dst)
    problems += compare("assets", static_src, assets_dst)
    problems += compare("python/static", static_src, py_static_dst)

    if problems:
        print(f"源码副本不一致（{len(problems)} 处）：", file=sys.stderr)
        for item in problems[:80]:
            print("  " + item, file=sys.stderr)
        if len(problems) > 80:
            print(f"  … 还有 {len(problems) - 80} 处", file=sys.stderr)
        print("请先运行：powershell -NoProfile -ExecutionPolicy Bypass -File android/copy_python.ps1", file=sys.stderr)
        return 1

    total = len(py_src) + len(static_src) * 2
    print(f"副本一致：python {len(py_src)} 个、static {len(static_src)} 个 ×2，共核对 {total} 项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
