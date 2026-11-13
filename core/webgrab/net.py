"""网络工具：线程本地会话、抓取、探测、文件名。

注意：requests.Session 不是线程安全的，视频分段下载并发时共享会话会串数据，
所以这里按线程各给一个 Session。
"""

from __future__ import annotations

import re
import threading
import warnings
from urllib.parse import unquote, urljoin, urlsplit

import requests

try:  # 只在降级 verify=False 时避免刷屏
    from urllib3.exceptions import InsecureRequestWarning

    warnings.filterwarnings("ignore", category=InsecureRequestWarning)
except Exception:  # pragma: no cover
    pass

UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
    "Mobile/15E148 Safari/604.1"
)
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_LOCAL = threading.local()

_CTYPE_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/avif": "avif",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "video/x-m4v": "m4v",
    "application/vnd.apple.mpegurl": "m3u8",
    "application/x-mpegurl": "m3u8",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
}

_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]')
_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"'，。；、）】》]+", re.IGNORECASE)


def session() -> requests.Session:
    """线程本地 requests.Session。"""
    sess = getattr(_LOCAL, "session", None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update(
            {
                "User-Agent": UA_IPHONE,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        _LOCAL.session = sess
    return sess


def _headers(referer: str = "", ua: str = "", extra: dict | None = None) -> dict:
    result = {
        "User-Agent": ua or UA_IPHONE,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        result["Referer"] = referer
    if extra:
        result.update(extra)
    return result


def http_get(
    url: str,
    *,
    referer: str = "",
    timeout: float = 20,
    allow_insecure: bool = True,
    ua: str = "",
) -> tuple[int, bytes, str]:
    """GET 页面，返回 (status, body, final_url)。

    SSL 证书错误时降级 verify=False 重试一次；网络错误继续向上抛。
    `ua` 是给扫描流程换桌面 UA 用的扩展参数，默认 iPhone Safari。
    """
    headers = _headers(referer, ua)
    try:
        resp = session().get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.exceptions.SSLError:
        if not allow_insecure:
            raise
        resp = session().get(
            url, headers=headers, timeout=timeout, allow_redirects=True, verify=False
        )
    return resp.status_code, resp.content, str(resp.url)


def stream_get(
    url: str,
    *,
    referer: str = "",
    timeout: tuple[float, float] = (10, 60),
    headers: dict | None = None,
    allow_insecure: bool = True,
) -> requests.Response:
    """流式 GET（下载/代理用）；SSL 错误降级一次。"""
    merged = _headers(referer, extra=headers)
    try:
        return session().get(
            url, headers=merged, timeout=timeout, stream=True, allow_redirects=True
        )
    except requests.exceptions.SSLError:
        if not allow_insecure:
            raise
        return session().get(
            url,
            headers=merged,
            timeout=timeout,
            stream=True,
            allow_redirects=True,
            verify=False,
        )


# ---------------------------------------------------------------- 图片尺寸


def image_size(data: bytes) -> tuple[int, int]:
    """从文件头解析常见图片宽高，失败返回 (0, 0)。"""
    try:
        if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if len(data) >= 10 and data[:6] in (b"GIF87a", b"GIF89a"):
            return (
                int.from_bytes(data[6:8], "little"),
                int.from_bytes(data[8:10], "little"),
            )
        if len(data) >= 26 and data[:2] == b"BM":
            return (
                abs(int.from_bytes(data[18:22], "little", signed=True)),
                abs(int.from_bytes(data[22:26], "little", signed=True)),
            )
        if len(data) >= 30 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X":
                w = int.from_bytes(data[24:27], "little") + 1
                h = int.from_bytes(data[27:30], "little") + 1
                return w, h
            if chunk == b"VP8L" and len(data) >= 25:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 " and len(data) >= 30:
                return (
                    int.from_bytes(data[26:28], "little") & 0x3FFF,
                    int.from_bytes(data[28:30], "little") & 0x3FFF,
                )
        if len(data) >= 4 and data[:2] == b"\xff\xd8":
            index = 2
            size = len(data)
            while index + 9 < size:
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                length = int.from_bytes(data[index + 2 : index + 4], "big")
                if marker in (
                    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                ):
                    return (
                        int.from_bytes(data[index + 7 : index + 9], "big"),
                        int.from_bytes(data[index + 5 : index + 7], "big"),
                    )
                if length <= 0:
                    break
                index += 2 + length
    except Exception:
        pass
    return 0, 0


def _int(value) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def _score_probe(resp: requests.Response) -> dict:
    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    size = _int(resp.headers.get("Content-Length"))
    return {"size": size, "ctype": ctype, "w": 0, "h": 0}


def _probe_raw(url: str, referer: str = "", timeout: float = 6) -> dict:
    """探测失败时抛 requests 异常（供 extract 判断同域超时）。"""
    headers = _headers(referer)
    result = {"size": 0, "ctype": "", "w": 0, "h": 0}
    head_ok = False
    try:
        resp = session().head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code < 400:
            head_ok = True
            partial = _score_probe(resp)
            result["size"] = partial["size"]
            result["ctype"] = partial["ctype"]
            if partial["size"] and partial["ctype"].startswith("image/"):
                # 图片还需要读内容拿宽高，继续走下面的 Range GET。
                pass
    except requests.exceptions.SSLError:
        resp = session().head(
            url, headers=headers, timeout=timeout, allow_redirects=True, verify=False
        )
        if resp.status_code < 400:
            head_ok = True
            partial = _score_probe(resp)
            result["size"] = partial["size"]
            result["ctype"] = partial["ctype"]
            if partial["size"] and partial["ctype"].startswith("image/"):
                # 同上：继续读内容拿宽高。
                pass
    except requests.exceptions.RequestException:
        if not head_ok:
            # HEAD 常常被站点拒绝，继续试 Range GET。
            pass

    range_headers = _headers(referer, extra={"Range": "bytes=0-262143"})
    try:
        resp = session().get(
            url, headers=range_headers, timeout=timeout, stream=True, allow_redirects=True
        )
    except requests.exceptions.SSLError:
        resp = session().get(
            url,
            headers=range_headers,
            timeout=timeout,
            stream=True,
            allow_redirects=True,
            verify=False,
        )
    try:
        partial = _score_probe(resp)
        result["ctype"] = result["ctype"] or partial["ctype"]
        result["size"] = result["size"] or partial["size"]
        content_range = resp.headers.get("Content-Range") or ""
        if "/" in content_range:
            total = _int(content_range.rsplit("/", 1)[1])
            if total:
                result["size"] = total
        if resp.status_code < 400:
            data = b""
            for chunk in resp.iter_content(65536):
                if not chunk:
                    continue
                data += chunk
                if len(data) >= 262144:
                    break
            if not result["ctype"]:
                result["ctype"] = _sniff_ctype(data)
            width, height = image_size(data)
            result["w"], result["h"] = width, height
    finally:
        resp.close()
    return result


def probe(url: str, *, referer: str = "", timeout: float = 6) -> dict:
    """探测媒体大小/类型/尺寸；失败返回全 0，不抛异常。"""
    try:
        return _probe_raw(url, referer=referer, timeout=timeout)
    except Exception:
        return {"size": 0, "ctype": "", "w": 0, "h": 0}


def _sniff_ctype(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":
        return "video/mp4"
    return ""


# ---------------------------------------------------------------- 文件名


def clean_filename(name: str, fallback: str = "download", max_len: int = 80) -> str:
    """清洗文件名：非法字符换成 _，长度不超过 80。"""
    text = _BAD_FILENAME.sub("_", str(name or "")).strip().strip(".")
    text = re.sub(r"\s+", " ", text)
    if not text:
        text = fallback or "download"
    return text[:max_len].strip() or (fallback or "download")


def filename_from(url: str, ctype: str = "", fallback: str = "download") -> tuple[str, str]:
    """从 URL/Content-Type 推断 (stem, ext)。"""
    path = urlsplit(url or "").path
    raw_name = unquote(path.rsplit("/", 1)[-1]) if path else ""
    ext = ""
    stem = raw_name
    if "." in raw_name:
        stem, candidate = raw_name.rsplit(".", 1)
        candidate = candidate.strip().lower()
        if 1 <= len(candidate) <= 5 and re.fullmatch(r"[a-z0-9]+", candidate):
            ext = candidate
        else:
            stem = raw_name
    if not ext:
        ext = _CTYPE_EXT.get((ctype or "").split(";")[0].strip().lower(), "")
    stem = clean_filename(stem, fallback=fallback, max_len=max(8, 80 - len(ext) - 1))
    return stem, ext


def human_size(n) -> str:
    """人类可读大小，例如 1.2 MB。"""
    try:
        value = float(n)
    except Exception:
        return "0 B"
    if value < 0:
        value = 0
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024 or unit == "TB":
            if value >= 100:
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def first_url(text: str) -> str:
    """从分享文案里取第一个链接；没有则返回原文本（去空白）。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    cleaned = raw.replace("\\/", "/")
    match = _URL_IN_TEXT.search(cleaned)
    if not match:
        return cleaned
    url = match.group(0)
    return url.rstrip(".,;:!?)]}>\"'，。；：！？）】》」』")


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def same_site(host_a: str, host_b: str) -> bool:
    a = (host_a or "").lower()
    b = (host_b or "").lower()
    if not a or not b:
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)
