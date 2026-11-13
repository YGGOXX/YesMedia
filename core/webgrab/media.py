"""ffmpeg / ffprobe 的统一封装（桌面 subprocess，Android FFmpegKit）。"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import net

_ANDROID: bool | None = None
_FFMPEG_DIR: str | None = None
_FFMPEG_LOCK = threading.Lock()


class MediaError(Exception):
    """媒体处理失败，消息直接展示给用户（中文）。"""


def is_android() -> bool:
    """运行环境是否 Android（Chaquopy / python-for-android）。"""
    global _ANDROID
    if _ANDROID is not None:
        return _ANDROID
    _ANDROID = False
    if os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_PRIVATE"):
        _ANDROID = True
    elif sys.platform.startswith("linux") and "ANDROID_DATA" in os.environ:
        _ANDROID = True
    else:
        try:  # Chaquopy：java 包只存在于 Android
            import java  # noqa: F401

            _ANDROID = True
        except Exception:
            _ANDROID = False
    return _ANDROID


def _search_dirs() -> list[Path]:
    dirs: list[Path] = []
    env_dir = os.environ.get("WEBGRAB_FFMPEG_DIR", "").strip()
    if env_dir:
        dirs.append(Path(env_dir))
        # 兼容“指向 ffmpeg 根目录、exe 在 bin/ 下”的布局
        dirs.append(Path(env_dir) / "bin")
    root = Path(__file__).resolve().parents[2]  # webgrab/
    dirs.append(root / "third_party" / "ffmpeg" / "bin")
    if getattr(sys, "_MEIPASS", None):
        dirs.append(Path(sys._MEIPASS) / "third_party" / "ffmpeg" / "bin")
        dirs.append(Path(sys._MEIPASS))
    dirs.append(Path(__file__).resolve().parent / "third_party" / "ffmpeg" / "bin")
    return dirs


def _tool(name: str) -> str:
    """返回 ffmpeg/ffprobe 可执行文件路径；找不到返回空串。"""
    candidates = [name, name + ".exe"] if os.name == "nt" else [name]
    for folder in _search_dirs():
        try:
            if not folder.is_dir():
                continue
        except OSError:
            continue
        for candidate in candidates:
            path = folder / candidate
            if path.is_file():
                return str(path)
    found = shutil.which(name)
    return found or ""


def ffmpeg_dir() -> str:
    """包含 ffmpeg 的目录（供 yt-dlp ffmpeg_location），没有则空串。"""
    global _FFMPEG_DIR
    if _FFMPEG_DIR is None:
        tool = _tool("ffmpeg")
        _FFMPEG_DIR = str(Path(tool).parent) if tool else ""
    return _FFMPEG_DIR


def ffmpeg_version() -> str:
    if is_android():
        try:
            from java import jclass  # type: ignore

            config = jclass("com.arthenica.ffmpegkit.FFmpegKitConfig")
            version = str(config.getFFmpegVersion() or "")
            return version or "ffmpeg-kit"
        except Exception:
            return ""
    tool = _tool("ffmpeg")
    if not tool:
        return ""
    try:
        result = subprocess.run(
            [tool, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        first = (result.stdout or "").splitlines()
        return first[0][:120] if first else ""
    except Exception:
        return ""


def ffmpeg_available() -> bool:
    if is_android():
        try:
            from java import jclass  # type: ignore

            jclass("com.arthenica.ffmpegkit.FFmpegKit")
            return True
        except Exception:
            return False
    return bool(_tool("ffmpeg"))


# --------------------------------------------------------------- 命令执行


def _quote_for_shell(cmd: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in cmd)


def _run_android(cmd: list[str], probe: bool = False) -> str:
    try:
        from java import jclass  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise MediaError("这个版本没有内置 ffmpeg") from exc
    klass_name = (
        "com.arthenica.ffmpegkit.FFprobeKit" if probe else "com.arthenica.ffmpegkit.FFmpegKit"
    )
    try:
        kit = jclass(klass_name)
        # FFmpegKit/FFprobeKit 会自动补上 "ffmpeg"/"ffprobe"，这里必须去掉 argv[0]
        args = cmd[1:] if cmd and str(cmd[0]).lower() in ("ffmpeg", "ffprobe") else cmd
        session = kit.execute(_quote_for_shell(args))
        return_code = session.getReturnCode()
        output = ""
        try:
            output = str(session.getOutput() or "")
        except Exception:
            output = ""
        ok = False
        if return_code is not None:
            try:
                ok = int(return_code.getValue()) == 0
            except Exception:
                try:
                    rcls = jclass("com.arthenica.ffmpegkit.ReturnCode")
                    ok = bool(rcls.isSuccess(return_code))
                except Exception:
                    ok = False
        if not ok:
            raise MediaError("媒体处理失败：" + _tail(output))
        return output
    except MediaError:
        raise
    except Exception as exc:  # pragma: no cover
        raise MediaError(f"媒体处理失败：{exc}") from exc


def _run(cmd: list[str], timeout: int = 600, probe: bool = False) -> str:
    if is_android():
        return _run_android(cmd, probe=probe)
    tool = cmd[0]
    resolved = _tool("ffprobe" if probe else "ffmpeg")
    if resolved:
        cmd = [resolved] + cmd[1:]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise MediaError("没有找到 ffmpeg，请检查安装") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError("媒体处理超时了") from exc
    if result.returncode != 0:
        detail = _tail((result.stderr or "") + "\n" + (result.stdout or ""))
        raise MediaError("媒体处理失败：" + detail)
    return result.stdout or ""


def _tail(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    return text[-limit:] if text else "未知错误"


# ------------------------------------------------------------------ 能力


def has_audio(path: str) -> bool:
    """判断文件是否含音轨。

    桌面端优先 ffprobe；只内置 ffmpeg 时退化为解析 `ffmpeg -i` 的输出。
    """
    if not path:
        return False
    if is_android():
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "a",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
        ]
        output = _run(cmd, timeout=60, probe=True).strip()
        return bool(output)
    tool = _tool("ffprobe")
    if tool:
        try:
            result = subprocess.run(
                [
                    tool, "-v", "error", "-select_streams", "a",
                    "-show_entries", "stream=index", "-of", "csv=p=0", str(path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise MediaError("检测音轨超时了") from exc
        if result.returncode != 0:
            return False
        return bool((result.stdout or "").strip())
    # 桌面端没有 ffprobe：ffmpeg 探测（返回码通常非 0，不看返回码）。
    ffmpeg = _tool("ffmpeg")
    if not ffmpeg:
        raise MediaError("没有找到 ffmpeg，无法检测音轨")
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return False
    output = (result.stderr or "") + "\n" + (result.stdout or "")
    return ": audio:" in output.lower()


# ------------------------------------------------------------------ 转换


_AUDIO_ARGS = {
    "mp3": ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"],
    "m4a": ["-vn", "-c:a", "aac", "-b:a", "128k"],
    "wav": ["-vn", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le"],
}


def convert_audio(src: str, fmt: str, out: str) -> str:
    fmt = (fmt or "mp3").lower()
    if fmt not in _AUDIO_ARGS:
        raise MediaError("不支持的音频格式")
    if not ffmpeg_available():
        raise MediaError("没有找到 ffmpeg，无法转换音频")
    if not has_audio(src):
        raise MediaError("这个文件里没有音轨")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", str(src), *_AUDIO_ARGS[fmt], str(out)]
    _run(cmd)
    return out


# ------------------------------------------------------------------ HLS


def _headers_arg(referer: str) -> list[str]:
    header = "Referer: " + (referer or "") + "\r\nUser-Agent: " + net.UA_DESKTOP + "\r\n"
    return ["-headers", header, "-user_agent", net.UA_DESKTOP]


def hls_to_mp4(url: str, referer: str, out: str) -> str:
    """m3u8 → mp4（ffmpeg 自动选最高码率，支持 AES-128）。"""
    if not ffmpeg_available():
        raise MediaError("没有找到 ffmpeg，无法处理 HLS")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    base = ["ffmpeg", "-y", *_headers_arg(referer), "-i", str(url)]
    try:
        _run([*base, "-c", "copy", "-bsf:a", "aac_adtstoasc",
              "-movflags", "+faststart", str(out)], timeout=900)
    except MediaError:
        _run([*base, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
              "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)],
             timeout=1800)
    if not Path(out).exists() or Path(out).stat().st_size == 0:
        raise MediaError("HLS 转封装失败")
    return out


# ------------------------------------------------------------------ 合并


def merge(video: str, audio: str, out: str) -> str:
    """把分离的视频/音频混流为 mp4。"""
    if not ffmpeg_available():
        raise MediaError("没有找到 ffmpeg，无法合并音视频")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(
            ["ffmpeg", "-y", "-i", str(video), "-i", str(audio),
             "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
             "-shortest", str(out)],
            timeout=1200,
        )
    except MediaError:
        _run(
            ["ffmpeg", "-y", "-i", str(video), "-i", str(audio),
             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "23", "-c:a", "aac",
             "-b:a", "128k", "-shortest", str(out)],
            timeout=2400,
        )
    if not Path(out).exists() or Path(out).stat().st_size == 0:
        raise MediaError("音视频合并失败")
    return out
