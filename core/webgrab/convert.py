"""音频转换：上传/链接 → 临时文件 → ffmpeg → 可下载产物（TTL 1 小时）。"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from . import config, engines, hls, media, net

MAX_UPLOAD = 800 * 1024 * 1024  # 800MB
FORMATS = ("mp3", "m4a", "wav")
JOB_TTL = 3600
CLEAN_INTERVAL = 600
CHUNK = 1024 * 1024


class ConvertError(Exception):
    """转换失败，消息直接展示给用户（中文）。"""


class TooLarge(ConvertError):
    """上传体超过 800MB。"""


_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_counter = [0]
_cleanup_started = [False]


def _convert_dir() -> Path:
    path = config.data_dir() / "convert"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tmp_dir() -> Path:
    path = config.data_dir() / "tmp" / "convert"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_id() -> str:
    with _jobs_lock:
        while True:
            _counter[0] += 1
            job_id = f"c{_counter[0]}"
            if job_id not in _jobs and not (_convert_dir() / f"{job_id}.mp3").exists():
                return job_id


def _display_name(filename: str, fmt: str, fallback: str = "audio") -> str:
    base = Path(str(filename or "")).name
    if "." in base:
        base = base.rsplit(".", 1)[0]
    stem = net.clean_filename(base, fallback=fallback, max_len=70)
    return f"{stem}.{fmt}"


def _register(path: Path, display: str, fmt: str) -> dict:
    job_id = _new_id()
    final = _convert_dir() / f"{job_id}.{fmt}"
    path.replace(final)
    job = {
        "id": job_id,
        "name": display,
        "size": final.stat().st_size,
        "path": final,
        "fmt": fmt,
        "created": time.time(),
        "url": f"/api/convert/file?id={job_id}",
    }
    with _jobs_lock:
        _jobs[job_id] = job
    return job


def _job_payload(job: dict) -> dict:
    return {
        "ok": True,
        "id": job["id"],
        "name": job["name"],
        "size": job["size"],
        "url": job["url"],
    }


def _fmt(value: str) -> str:
    fmt = (value or "mp3").strip().lower().lstrip(".")
    if fmt not in FORMATS:
        raise ConvertError("不支持的格式，只能转 mp3/m4a/wav")
    return fmt


def _check_size(total: int) -> None:
    if total > MAX_UPLOAD:
        raise TooLarge("文件太大了，最大支持 800MB")


def save_upload(stream, filename: str, fmt: str, content_length=None) -> dict:
    """把裸 body 写成临时文件并转换；stream 为 file-like（.read）。"""
    fmt = _fmt(fmt)
    remaining = None
    if content_length is not None:
        try:
            remaining = int(content_length)
        except (TypeError, ValueError):
            remaining = None
    if remaining is not None:
        _check_size(remaining)

    tmp_input = _tmp_dir() / f"upload_{uuid.uuid4().hex}.bin"
    total = 0
    try:
        with open(tmp_input, "wb") as handle:
            # 必须按 Content-Length 读，HTTP keep-alive 下 read() 读到 EOF 会一直等。
            while remaining is None or remaining > 0:
                want = CHUNK if remaining is None else min(CHUNK, remaining)
                chunk = stream.read(want)
                if not chunk:
                    break
                total += len(chunk)
                _check_size(total)
                handle.write(chunk)
                if remaining is not None:
                    remaining -= len(chunk)
        if total == 0:
            raise ConvertError("没有收到文件内容")
        display = _display_name(filename, fmt)
        output = _tmp_dir() / f"out_{uuid.uuid4().hex}.{fmt}"
        media.convert_audio(str(tmp_input), fmt, str(output))
        job = _register(output, display, fmt)
        return _job_payload(job)
    finally:
        try:
            tmp_input.unlink()
        except OSError:
            pass


def _download_direct(url: str, referer: str) -> Path:
    target = _tmp_dir() / f"link_{uuid.uuid4().hex}.bin"
    response = net.stream_get(url, referer=referer or "", timeout=(20, 180))
    try:
        if response.status_code >= 400:
            raise ConvertError(f"下载失败：HTTP {response.status_code}")
        written = 0
        with open(target, "wb") as handle:
            for chunk in response.iter_content(CHUNK):
                if not chunk:
                    continue
                written += len(chunk)
                _check_size(written)
                handle.write(chunk)
        if written == 0:
            raise ConvertError("下载失败：文件是空的")
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    finally:
        response.close()
    return target


def convert_link(url: str, referer: str, via: str, fmt: str, name: str = "") -> dict:
    """从链接（direct/hls/yt）取回媒体后转音频。"""
    fmt = _fmt(fmt)
    link = (url or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        raise ConvertError("链接地址不正确")
    via = (via or "direct").strip().lower()
    if via not in ("direct", "hls", "yt"):
        raise ConvertError("不支持的来源类型")

    source: Path | None = None
    display_name = name or ""
    try:
        if via == "hls":
            source = _tmp_dir() / f"hls_{uuid.uuid4().hex}.mp4"
            hls.download_hls(link, referer or "", source)
        elif via == "yt":
            work = _tmp_dir() / f"yt_{uuid.uuid4().hex}"
            work.mkdir(parents=True, exist_ok=True)
            path, yt_name = engines.ytdlp_download(link, work)
            source = Path(path)
            display_name = display_name or yt_name
        else:
            source = _download_direct(link, referer or "")

        if not source.exists() or source.stat().st_size == 0:
            raise ConvertError("没有拿到可转换的文件")
        display = _display_name(
            display_name or net.filename_from(link, fallback="audio")[0], fmt
        )
        output = _tmp_dir() / f"out_{uuid.uuid4().hex}.{fmt}"
        media.convert_audio(str(source), fmt, str(output))
        job = _register(output, display, fmt)
        return _job_payload(job)
    finally:
        if source is not None:
            try:
                source.unlink()
            except OSError:
                pass
            parent = source.parent
            while parent != _tmp_dir() and parent.name.startswith("yt_"):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent


def get_job(job_id: str) -> dict | None:
    job_id = (job_id or "").strip()
    if not job_id:
        return None
    _cleanup_once()
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return None
    path = Path(job["path"])
    if not path.exists():
        with _jobs_lock:
            _jobs.pop(job_id, None)
        return None
    return job


def _cleanup_once() -> None:
    now = time.time()
    expired = []
    with _jobs_lock:
        for job_id, job in list(_jobs.items()):
            if now - job.get("created", 0) > JOB_TTL:
                expired.append(job_id)
        for job_id in expired:
            _jobs.pop(job_id, None)
    for job_id in expired:
        for fmt in FORMATS:
            try:
                (_convert_dir() / f"{job_id}.{fmt}").unlink()
            except OSError:
                pass


def cleanup_loop() -> None:
    # 启动时清掉上次残留
    try:
        for folder in (_tmp_dir(), _convert_dir()):
            try:
                for entry in folder.iterdir():
                    if entry.is_file() and time.time() - entry.stat().st_mtime > JOB_TTL:
                        entry.unlink()
            except OSError:
                pass
    except Exception:
        pass
    while True:
        time.sleep(CLEAN_INTERVAL)
        try:
            _cleanup_once()
        except Exception:
            pass


def start_cleanup() -> None:
    with _jobs_lock:
        if _cleanup_started[0]:
            return
        _cleanup_started[0] = True
    thread = threading.Thread(
        target=cleanup_loop, name="convert-cleanup", daemon=True
    )
    thread.start()
