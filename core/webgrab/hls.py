"""HLS（m3u8）下载：交给 media/ffmpeg 完成转封装。"""

from __future__ import annotations

from pathlib import Path

from . import media


class HlsError(Exception):
    """HLS 处理失败，消息直接展示给用户（中文）。"""


def _report(progress, stage: str, msg: str) -> None:
    if not progress:
        return
    try:
        progress(stage, msg)
    except Exception:
        pass


def download_hls(url: str, referer: str, out_path, progress=None) -> str:
    """把 m3u8 下载并转封装为 mp4；成功后返回产物路径。"""
    link = (url or "").strip()
    if not link.lower().startswith(("http://", "https://")):
        raise HlsError("播放列表地址不正确")
    if not media.ffmpeg_available():
        raise HlsError("没有找到 ffmpeg，无法处理 HLS 视频")

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _report(progress, "hls", "解析播放列表")
    try:
        media.hls_to_mp4(link, referer or "", str(target))
    except media.MediaError as exc:
        raise HlsError(str(exc) or "HLS 下载失败") from exc
    if not target.exists() or target.stat().st_size == 0:
        raise HlsError("HLS 下载失败：没有生成文件")
    _report(progress, "hls", "转封装完成")
    return str(target)
