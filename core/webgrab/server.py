"""WebGrab 本地 HTTP 服务（纯标准库 ThreadingHTTPServer）。"""

from __future__ import annotations

import argparse
import json
import mimetypes
import queue
import re
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import __version__, config, convert, engines, extract, hls, media, net

_STATIC_DEFAULT = Path(__file__).resolve().parent / "static"

_server: "_Server | None" = None
_thread: threading.Thread | None = None
_lock = threading.Lock()

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}
_NO_CACHE_EXT = {".html", ".htm", ".js", ".mjs", ".css", ".webmanifest"}
_MAX_JSON = 2 * 1024 * 1024


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed, _encoding = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _download_disposition(name: str) -> str:
    clean = net.clean_filename(name, fallback="download", max_len=120)
    ascii_name = clean.encode("ascii", "replace").decode("ascii")
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(clean, safe='')}"


_EXT_BY_TYPE = {
    "video/mp4": "mp4",
    "video/quicktime": "mov",
    "video/webm": "webm",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def _download_name(name: str, url: str, ctype: str = "", fallback_ext: str = "") -> str:
    if name:
        clean = net.clean_filename(name, fallback="download", max_len=120)
        # 某些 Android 下载器会截断含 # 的文件名，导致扩展名丢失；按响应类型补回。
        if "." not in clean.rsplit("/", 1)[-1]:
            _stem, ext = net.filename_from(url, ctype or "", fallback="download")
            if not ext:
                ext = fallback_ext or _EXT_BY_TYPE.get((ctype or "").split(";")[0].strip().lower(), "")
            if ext:
                clean = f"{clean}.{ext}"
        return clean
    stem, ext = net.filename_from(url, ctype, fallback="download")
    if not ext and fallback_ext:
        ext = fallback_ext
    return f"{stem}.{ext}" if ext else stem


def _is_html_type(ctype: str) -> bool:
    text = (ctype or "").lower()
    return "text/html" in text or "application/xhtml" in text


def _refresh_douyin_item(page_url: str) -> dict | None:
    """直链过期时绕过缓存重解析一次，返回新的视频 item。"""
    try:
        result = engines.resolve_douyin(page_url, None, use_cache=False)
    except Exception:
        return None
    for item in result.get("items") or []:
        if (item.get("kind") or "video") == "video" and item.get("url"):
            return item
    return None


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:  # 浏览器断连时不要打堆栈
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "WebGrab/" + __version__
    sys_version = ""

    # ------------------------------------------------------------ 入口

    def do_GET(self) -> None:
        self._wrap(self._get)

    def do_HEAD(self) -> None:
        self._wrap(self._get)

    def do_POST(self) -> None:
        self._wrap(self._post)

    def log_message(self, fmt: str, *args) -> None:
        # 契约要求不把用户 URL 写进日志，这里直接静默。
        pass

    def _wrap(self, func) -> None:
        try:
            func()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception:
            if not getattr(self, "_headers_sent", False):
                try:
                    self._json(200, {"ok": False, "error": "服务出错了，请重试"})
                except Exception:
                    pass
            self.close_connection = True

    def _get(self) -> None:
        if not self._host_allowed():
            self._json(403, {"ok": False, "error": "只能从本机访问"})
            return
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query, keep_blank_values=True)
        if path.startswith("/api/"):
            self._api_get(path, query)
        else:
            self._serve_static(path)

    def _post(self) -> None:
        origin = (self.headers.get("Origin") or "").strip()
        host = (self.headers.get("Host") or "").strip().lower()
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if not origin or origin.lower() != ("http://" + host) or fetch_site in ("cross-site", "same-site"):
            self._json(403, {"ok": False, "error": "请求来源不受允许"})
            return
        if not self._host_allowed():
            self._json(403, {"ok": False, "error": "只能从本机访问"})
            return
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query, keep_blank_values=True)
        if path == "/api/scan":
            self._api_scan(query, body=self._read_json(silent=True))
        elif path == "/api/scan_stream":
            self._api_scan_stream(query, body=self._read_json(silent=True))
        elif path == "/api/config":
            self._api_config_post()
        elif path == "/api/convert/upload":
            self._api_convert_upload(query)
        elif path == "/api/convert/link":
            self._api_convert_link()
        else:
            self._json(404, {"ok": False, "error": "接口不存在"})

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return True
        if host.startswith("["):
            name = host.split("]")[0] + "]"
            return name in ("[::1]",)
        name = host
        if ":" in host:
            maybe_name, maybe_port = host.rsplit(":", 1)
            if maybe_port.isdigit():
                name = maybe_name
        return name in ("127.0.0.1", "localhost")

    # --------------------------------------------------------- 基础输出

    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._headers_sent = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _error(self, message: str, code: str = "") -> None:
        payload = {"ok": False, "error": message or "操作失败，请重试"}
        if code:
            payload["error_code"] = code
        self._json(200, payload)

    def _query(self, query: dict, name: str, default: str = "") -> str:
        values = query.get(name)
        return values[0] if values else default

    def _read_json(self, silent: bool = False) -> dict:
        length = self.headers.get("Content-Length")
        if not length:
            return {}
        try:
            size = int(length)
        except (TypeError, ValueError):
            return {}
        if size <= 0 or size > _MAX_JSON:
            return {}
        try:
            raw = self.rfile.read(size)
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return {}
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"_body": data}
        return {}

    # ------------------------------------------------------------ 路由

    def _api_get(self, path: str, query: dict) -> None:
        if path == "/api/health":
            available = media.ffmpeg_available()
            self._json(
                200,
                {
                    "ok": True,
                    "version": __version__,
                    "ffmpeg": available,
                    "convert": available,
                    "platform": "android" if media.is_android() else "windows",
                    "port": int(self.server.port),
                },
            )
        elif path == "/api/config":
            self._json(200, {"ok": True, "douyin_apis": config.douyin_apis()})
        elif path == "/api/scan":
            self._api_scan(query, body={})
        elif path == "/api/scan_stream":
            self._api_scan_stream(query, body={})
        elif path == "/api/dl":
            self._api_download(query)
        elif path == "/api/proxy":
            self._api_proxy(query)
        elif path == "/api/hls":
            self._api_hls(query)
        elif path == "/api/yt":
            self._api_yt(query)
        elif path == "/api/convert/file":
            self._api_convert_file(query)
        else:
            self._json(404, {"ok": False, "error": "接口不存在"})

    # ------------------------------------------------------------ 扫描

    @staticmethod
    def _tp(query: dict, body: dict) -> bool:
        value = str(body.get("allow_third_party", "")).strip().lower()
        if not value:
            value = str(body.get("tp", "")).strip().lower()
        if not value:
            value = str(query.get("tp", [""])[0]).strip().lower()
        return value in ("1", "true", "yes", "on")

    def _api_scan(self, query: dict, body: dict) -> None:
        url = str(body.get("url") or self._query(query, "url")).strip()
        try:
            result = extract.scan_page(url, self._tp(query, body))
        except extract.ScanError as exc:
            self._error(str(exc), code=str(getattr(exc, "code", "") or ""))
            return
        except Exception:
            self._error("扫描失败，请稍后再试", code="PAGE_LOAD_FAILED")
            return
        self._json(200, result)

    def _api_scan_stream(self, query: dict, body: dict) -> None:
        url = str(body.get("url") or self._query(query, "url")).strip()
        third_party = self._tp(query, body)
        self._start_events()
        self._event({"stage": "open", "msg": "打开页面"})

        events: "queue.Queue[dict | None]" = queue.Queue(maxsize=128)
        state = {"gone": False}

        def emit(payload: dict) -> None:
            try:
                events.put(payload, timeout=1)
            except queue.Full:
                pass

        def progress(stage: str, msg: str) -> None:
            if state["gone"] or not stage or stage == "open":
                return
            payload = {"stage": stage, "msg": msg}
            if stage == "probe":
                match = re.search(r"(\d+)", str(msg or ""))
                if match:
                    payload["found"] = int(match.group(1))
            emit(payload)

        def worker() -> None:
            try:
                result = extract.scan_page(url, third_party, progress=progress)
            except extract.ScanError as exc:
                emit({
                    "stage": "done",
                    "ok": False,
                    "error": str(exc),
                    "error_code": str(getattr(exc, "code", "") or ""),
                })
            except Exception:
                emit({
                    "stage": "done",
                    "ok": False,
                    "error": "扫描失败，请稍后再试",
                    "error_code": "PAGE_LOAD_FAILED",
                })
            else:
                emit({"stage": "done", "ok": True, "result": result})
            finally:
                emit(None)

        threading.Thread(target=worker, name="webgrab-scan-sse", daemon=True).start()

        # 心跳：客户端 8s 空闲会回退 POST；扫描（尤其抖音桥）可能 8–25s 没有事件，
        # 每 3s 发一条 ping 让前端保持 SSE，避免重复创建解析任务。
        deadline = time.time() + 240
        while True:
            try:
                payload = events.get(timeout=3)
            except queue.Empty:
                if not self._event({"stage": "ping"}):
                    state["gone"] = True
                    break
                if time.time() > deadline:
                    state["gone"] = True
                    break
                continue
            if payload is None:
                break
            if not self._event(payload):
                state["gone"] = True
                break
        self.close_connection = True

    # ------------------------------------------------------- SSE 输出

    def _start_events(self) -> None:
        self._headers_sent = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

    def _event(self, payload: dict) -> bool:
        line = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        try:
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ 配置

    def _api_config_post(self) -> None:
        body = self._read_json()
        apis = body.get("douyin_apis") if isinstance(body, dict) else None
        if apis is None and "_body" in body:
            apis = body["_body"]
        error = config.validate_douyin_apis(apis)
        if error:
            self._error(error)
            return
        try:
            config.save_config(apis)
        except OSError:
            self._error("保存配置失败，请检查磁盘权限")
            return
        self._json(200, {"ok": True, "douyin_apis": config.douyin_apis()})

    # ------------------------------------------------------ 下载 / 代理

    def _checked_url(self, url: str) -> bool:
        return url.lower().startswith(("http://", "https://"))

    def _api_download(self, query: dict) -> None:
        url = self._query(query, "u").strip()
        referer = self._query(query, "ref").strip()
        name = self._query(query, "name").strip()
        page = self._query(query, "page").strip()
        via = self._query(query, "via").strip()
        if not self._checked_url(url):
            self._error("下载地址不正确")
            return
        range_headers = None
        client_range = self.headers.get("Range")
        if client_range:
            range_headers = {"Range": client_range}
        douyin_hint = via == "dy" or engines.is_douyin(page) or engines.is_douyin(url)
        retried = False
        while True:
            try:
                upstream = net.stream_get(
                    url, referer=referer, timeout=(15, 300), headers=range_headers
                )
            except Exception:
                self._error("下载失败：网络连不上")
                return
            try:
                ctype_header = upstream.headers.get("Content-Type") or ""
                expired = upstream.status_code in (403, 404) or _is_html_type(ctype_header)
                if expired and douyin_hint and page:
                    if retried:
                        self._error("抖音直链已过期，请重新解析")
                        return
                    fresh = _refresh_douyin_item(page)
                    fresh_url = str((fresh or {}).get("url") or "").strip()
                    if not fresh or not self._checked_url(fresh_url):
                        self._error("抖音直链已过期，请重新解析")
                        return
                    url = fresh_url
                    referer = str(fresh.get("referer") or referer or "https://www.douyin.com/").strip()
                    name = name or str(fresh.get("name") or "").strip()
                    retried = True
                    continue
                if upstream.status_code >= 400:
                    self._error(f"下载失败（HTTP {upstream.status_code}）")
                    return
                ctype = (ctype_header or "application/octet-stream").split(";")[0]
                filename = _download_name(name, url, ctype)
                status = 206 if upstream.status_code == 206 and upstream.headers.get("Content-Range") else 200
                headers = {
                    "Content-Type": ctype or "application/octet-stream",
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": _download_disposition(filename),
                }
                if status == 206:
                    headers["Content-Range"] = upstream.headers.get("Content-Range", "")
                length = upstream.headers.get("Content-Length")
                if length:
                    headers["Content-Length"] = length
                else:
                    self.close_connection = True
                self._begin(status, headers)
                self._pump(upstream)
                return
            finally:
                upstream.close()

    def _api_proxy(self, query: dict) -> None:
        url = self._query(query, "u").strip()
        referer = self._query(query, "ref").strip()
        if not self._checked_url(url):
            self._error("预览地址不正确")
            return
        extra_headers = {}
        client_range = self.headers.get("Range")
        if client_range:
            extra_headers["Range"] = client_range
        try:
            upstream = net.stream_get(
                url, referer=referer, timeout=(15, 120), headers=extra_headers or None
            )
        except Exception:
            self._error("预览失败：网络连不上")
            return
        try:
            if upstream.status_code >= 400:
                self._error(f"预览失败（HTTP {upstream.status_code}）")
                return
            status = 206 if upstream.status_code == 206 else 200
            headers = {}
            for key in (
                "Content-Type", "Content-Length", "Content-Range",
                "Accept-Ranges", "Last-Modified", "ETag",
            ):
                value = upstream.headers.get(key)
                if value:
                    headers[key] = value
            if status == 206 and "Content-Range" not in headers:
                status = 200
            headers.setdefault("Accept-Ranges", "bytes")
            if "Content-Length" not in headers:
                self.close_connection = True
            self._begin(status, headers)
            self._pump(upstream)
        finally:
            upstream.close()

    # ---------------------------------------------------------- HLS / yt

    def _api_hls(self, query: dict) -> None:
        url = self._query(query, "u").strip()
        referer = self._query(query, "ref").strip()
        name = self._query(query, "name").strip()
        if not self._checked_url(url):
            self._error("播放列表地址不正确")
            return
        tmp = config.data_dir() / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        out = tmp / f"hls_{uuid.uuid4().hex}.mp4"
        try:
            hls.download_hls(url, referer, str(out))
        except Exception as exc:
            self._error(str(exc) or "HLS 下载失败")
            return
        try:
            display = _download_name(
                name or net.filename_from(url, fallback="video")[0] + ".mp4", url, "video/mp4"
            )
            self._serve_file(out, download_name=display)
        finally:
            try:
                out.unlink()
            except OSError:
                pass

    def _api_yt(self, query: dict) -> None:
        url = self._query(query, "u").strip()
        referer = self._query(query, "ref").strip()
        name = self._query(query, "name").strip()
        if not self._checked_url(url):
            self._error("视频地址不正确")
            return
        tmp = config.data_dir() / "tmp" / f"yt_{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        path = None
        try:
            path, filename = engines.ytdlp_download(url, str(tmp))
        except engines.EngineError as exc:
            self._error(str(exc))
            return
        except Exception:
            self._error("视频下载失败，请稍后再试")
            return
        finally:
            if path is None:
                self._remove_dir(tmp)
        try:
            display = _download_name(name or filename, url, "", fallback_ext="mp4")
            self._serve_file(Path(path), download_name=display)
        finally:
            try:
                Path(path).unlink()
            except OSError:
                pass
            self._remove_dir(tmp)

    @staticmethod
    def _remove_dir(path: Path) -> None:
        try:
            for entry in path.iterdir():
                try:
                    entry.unlink()
                except OSError:
                    pass
            path.rmdir()
        except OSError:
            pass

    # ---------------------------------------------------------- 转音频

    def _api_convert_upload(self, query: dict) -> None:
        fmt = self._query(query, "fmt", "mp3")
        length = self.headers.get("Content-Length")
        try:
            size = int(length) if length is not None else None
        except (TypeError, ValueError):
            size = None
        if size is None or size <= 0:
            self.close_connection = True
            self._error("没有收到文件内容")
            return
        if size > convert.MAX_UPLOAD:
            self.close_connection = True
            self._json(413, {"ok": False, "error": "文件太大了，最大支持 800MB"})
            return
        filename = unquote(self.headers.get("X-Filename") or "")
        try:
            payload = convert.save_upload(self.rfile, filename, fmt, size)
        except convert.TooLarge as exc:
            self._json(413, {"ok": False, "error": str(exc)})
            return
        except (convert.ConvertError, media.MediaError) as exc:
            self._error(str(exc))
            return
        except Exception:
            self._error("转换失败，请稍后再试")
            return
        self._json(200, payload)

    def _api_convert_link(self) -> None:
        body = self._read_json()
        url = str(body.get("u") or "").strip()
        referer = str(body.get("ref") or "").strip()
        via = str(body.get("via") or "direct").strip()
        fmt = str(body.get("fmt") or "mp3").strip()
        name = str(body.get("name") or "").strip()
        try:
            payload = convert.convert_link(url, referer, via, fmt, name)
        except (convert.ConvertError, media.MediaError, hls.HlsError) as exc:
            self._error(str(exc))
            return
        except engines.EngineError as exc:
            self._error(str(exc))
            return
        except Exception:
            self._error("转换失败，请稍后再试")
            return
        self._json(200, payload)

    def _api_convert_file(self, query: dict) -> None:
        job_id = self._query(query, "id").strip()
        job = convert.get_job(job_id)
        if not job:
            self._error("文件已过期，请重新转换")
            return
        path = Path(job["path"])
        if not path.exists():
            self._error("文件已过期，请重新转换")
            return
        self._serve_file(path, download_name=str(job["name"]))

    # ------------------------------------------------------------ 静态

    def _static_rel(self, path: str) -> str:
        if path in ("/", "/index.html", "/share"):
            return "index.html"
        if path in ("/demo", "/demo/"):
            return "demo.html"
        if path == "/manifest.webmanifest":
            return "manifest.webmanifest"
        if path == "/sw.js":
            return "sw.js"
        if path == "/favicon.ico":
            return "favicon.ico"
        if path.startswith("/static/"):
            return path[len("/static/"):]
        if path.startswith("/icons/"):
            return path[len("/"):]
        if path.startswith("/demo/"):
            return path[len("/"):]
        return ""

    def _serve_static(self, path: str) -> None:
        rel = self._static_rel(path)
        if not rel:
            self._not_found()
            return
        root = Path(self.server.static_root)
        candidate = (root / rel).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._not_found()
            return
        if not candidate.is_file():
            fallback = (root / "static" / rel).resolve()
            try:
                fallback.relative_to(root)
            except ValueError:
                fallback = None
            if fallback is not None and fallback.is_file():
                candidate = fallback
        if not candidate.is_file():
            self._not_found()
            return
        self._serve_file(candidate)

    def _not_found(self) -> None:
        data = "404 Not Found".encode("utf-8")
        self._headers_sent = True
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _serve_file(self, path: Path, download_name: str = "") -> None:
        try:
            size = path.stat().st_size
        except OSError:
            self._not_found()
            return
        start = 0
        end = size - 1
        partial = False
        range_header = self.headers.get("Range") or ""
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if match and size > 0:
            first, second = match.group(1), match.group(2)
            if first:
                start = int(first)
                end = int(second) if second else size - 1
            elif second:
                length = int(second)
                start = max(0, size - length)
                end = size - 1
            else:
                start = size
            if start >= size or start > end:
                self._headers_sent = True
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True

        length = end - start + 1 if size > 0 else 0
        suffix = path.suffix.lower()
        headers = {
            "Content-Type": _mime(path),
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-cache" if suffix in _NO_CACHE_EXT else "public, max-age=3600",
        }
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        if download_name:
            headers["Content-Disposition"] = _download_disposition(download_name)
        status = 206 if partial else 200
        self._begin(status, headers)
        if self.command == "HEAD" or length == 0:
            return
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            if remaining:
                self.close_connection = True
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except OSError:
            self.close_connection = True

    # ------------------------------------------------------- 头发送

    def _begin(self, status: int, headers: dict) -> None:
        self._headers_sent = True
        self.send_response(status)
        for key, value in headers.items():
            if value is None or value == "":
                continue
            self.send_header(key, str(value))
        self.end_headers()

    def _pump(self, upstream) -> None:
        try:
            for chunk in upstream.iter_content(65536):
                if not chunk:
                    continue
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception:
            self.close_connection = True


# --------------------------------------------------------------- 启停


def start(static_dir=None, port: int = 8765) -> int:
    """绑定 127.0.0.1 的 8765..8799 第一个可用端口，后台线程服务，返回端口。"""
    global _server, _thread
    with _lock:
        if _server is not None:
            return int(_server.port)
        root = Path(static_dir).resolve() if static_dir else _STATIC_DEFAULT
        httpd = None
        start_port = int(port)
        for candidate in range(start_port, 8800):
            try:
                httpd = _Server(("127.0.0.1", candidate), Handler)
                break
            except OSError:
                continue
        if httpd is None:
            raise OSError(f"端口 {start_port}-8799 都被占用了，请关掉占用的程序再试")
        httpd.static_root = root
        httpd.port = int(httpd.server_address[1])
        _server = httpd
        _thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="webgrab-server",
            daemon=True,
        )
        _thread.start()
    convert.start_cleanup()
    return int(httpd.port)


def serve(port: int = 8765, static_dir=None) -> int:
    """兼容契约命名：serve(port, static_dir) -> 实际端口。"""
    return start(static_dir, port)


def stop() -> None:
    """停止后台服务并回收线程。"""
    global _server, _thread
    with _lock:
        httpd = _server
        thread = _thread
        _server = None
        _thread = None
    if httpd is not None:
        try:
            httpd.shutdown()
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
    if thread is not None:
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(prog="webgrab.server", description="WebGrab 本地服务")
    parser.add_argument("--port", type=int, default=8765, help="起始端口（8765-8799）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    try:
        port = start(_STATIC_DEFAULT, args.port)
    except OSError as exc:
        print(f"[webgrab] 启动失败：{exc}")
        raise SystemExit(1) from exc
    print(f"WEBGRAB_URL=http://127.0.0.1:{port}", flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        except Exception:
            pass
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        stop()


if __name__ == "__main__":
    main()
