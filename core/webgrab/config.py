"""数据目录与 config.json 读写。

约定：
- 数据目录优先取环境变量 WEBGRAB_HOME，否则 ~/.webgrab。
- 抖音接口默认一条内置模板，可用环境变量 DOUYIN_API 覆盖。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

DEFAULT_DOUYIN_APIS = [
    "https://api.xingzhige.com/API/douyin/?url={url}",
]

_LOCK = threading.RLock()
_CFG_CACHE: dict | None = None
_DATA_DIRS: dict[str, Path] = {}


def data_dir() -> Path:
    """返回数据目录（不存在则创建）。

    WEBGRAB_HOME 优先，否则 ~/.webgrab；两者都不可写时退回系统临时目录，
    保证服务仍能启动（转换产物本来就是临时的）。
    """
    raw = os.environ.get("WEBGRAB_HOME", "").strip()
    with _LOCK:
        cached = _DATA_DIRS.get(raw)
        if cached is not None:
            return cached
        path = Path(raw).expanduser() if raw else Path.home() / ".webgrab"
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            path = Path(tempfile.gettempdir()) / "webgrab"
            path.mkdir(parents=True, exist_ok=True)
        _DATA_DIRS[raw] = path
        return path


def config_path() -> Path:
    return data_dir() / "config.json"


def default_config() -> dict:
    apis = list(DEFAULT_DOUYIN_APIS)
    env = os.environ.get("DOUYIN_API", "").strip()
    if env:
        apis = [env]
    return {"douyin_apis": apis}


def validate_douyin_apis(apis) -> str:
    """校验接口数组；合法返回空串，否则返回中文原因。"""
    if not isinstance(apis, list):
        return "抖音接口必须是数组"
    if not 1 <= len(apis) <= 5:
        return "抖音接口需要 1 到 5 条"
    for index, api in enumerate(apis, 1):
        if not isinstance(api, str):
            return f"第 {index} 条接口不是文本"
        api = api.strip()
        if not (api.startswith("http://") or api.startswith("https://")):
            return f"第 {index} 条接口必须以 http:// 或 https:// 开头"
        if "{url}" not in api:
            return f"第 {index} 条接口缺少 {{url}} 占位符"
    return ""


def load_config() -> dict:
    global _CFG_CACHE
    with _LOCK:
        if _CFG_CACHE is not None:
            return _CFG_CACHE
        cfg = default_config()
        path = config_path()
        try:
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    apis = raw.get("douyin_apis")
                    if apis is not None and not validate_douyin_apis(apis):
                        cfg["douyin_apis"] = [str(a).strip() for a in apis]
        except Exception:
            # 配置坏了就用默认值，不影响服务启动。
            pass
        _CFG_CACHE = cfg
        return cfg


def save_config(apis) -> dict:
    """保存接口数组；非法抛 ValueError（消息为中文）。"""
    global _CFG_CACHE
    error = validate_douyin_apis(apis)
    if error:
        raise ValueError(error)
    clean = [a.strip() for a in apis]
    with _LOCK:
        cfg = dict(load_config())
        cfg["douyin_apis"] = clean
        path = config_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        _CFG_CACHE = cfg
        return cfg


def douyin_apis() -> list[str]:
    return [str(a) for a in (load_config().get("douyin_apis") or [])]


def reset_cache() -> None:
    """测试用：清掉内存里的配置缓存。"""
    global _CFG_CACHE
    with _LOCK:
        _CFG_CACHE = None
