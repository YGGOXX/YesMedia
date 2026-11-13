"""Desktop launcher for 网页抓取器 (WebGrab)."""
import ctypes
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_NAME = "网页抓取器"
INSTANCE_PORT = 18765
log = logging.getLogger("webgrab.launcher")


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def setup_environment() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    # Honour an explicit override (portable/test runs); default stays %LOCALAPPDATA%.
    home = Path(os.environ.get("WEBGRAB_HOME") or (Path(local) / "WebGrab"))
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WEBGRAB_HOME"] = str(home)
    root = base_dir()
    ffdir = root / "third_party" / "ffmpeg" / "bin"
    if (ffdir / "ffmpeg.exe").exists():
        os.environ["WEBGRAB_FFMPEG_DIR"] = str(ffdir)
    # Never route localhost through a corporate/system proxy.
    no_proxy = os.environ.get("NO_PROXY", "")
    for host in ("127.0.0.1", "localhost"):
        if host not in no_proxy:
            no_proxy = (no_proxy + "," + host).strip(",")
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy
    return home


def setup_logging(home: Path) -> None:
    handlers = [logging.FileHandler(home / "webgrab.log", encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def fatal(message: str) -> None:
    log.error(message)
    try:
        ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x10)
    except Exception:
        pass
    sys.exit(1)


def acquire_instance(home: Path) -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", INSTANCE_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def open_existing(home: Path) -> None:
    url_file = home / "url.txt"
    for _ in range(20):
        if url_file.exists():
            url = url_file.read_text(encoding="utf-8").strip()
            if url.startswith("http://127.0.0.1:"):
                webbrowser.open(url)
                return
        time.sleep(0.1)
    webbrowser.open("http://127.0.0.1:8765/")


def make_image(root: Path):
    try:
        from PIL import Image
        p = root / "webgrab" / "static" / "icons" / "icon-192.png"
        if p.exists():
            return Image.open(p).convert("RGBA").resize((64, 64))
    except Exception:
        pass
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((2, 2, 62, 62), radius=16, fill=(94, 129, 255, 255))
        d.line((32, 14, 32, 42), fill=(255, 255, 255, 255), width=7)
        d.polygon([(20, 34), (44, 34), (32, 50)], fill=(255, 255, 255, 255))
        return img
    except Exception:
        return None


def run_tray(home: Path, url: str, shutdown) -> bool:
    try:
        import pystray
        from pystray import Menu, MenuItem
    except Exception as exc:  # tray is optional
        log.warning("tray unavailable: %s", exc)
        return False
    image = make_image(base_dir())
    if image is None:
        return False

    def on_open(icon, item):
        webbrowser.open(url)

    def on_quit(icon, item):
        shutdown()
        icon.stop()
        os._exit(0)

    menu = Menu(
        MenuItem("打开网页", on_open, default=True),
        MenuItem("打开下载文件夹", lambda i, it: os.startfile(str(Path.home() / "Downloads"))),
        Menu.SEPARATOR,
        MenuItem("退出", on_quit),
    )
    icon = pystray.Icon("webgrab", image, APP_NAME, menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return True


def main() -> None:
    home = setup_environment()
    setup_logging(home)

    instance = acquire_instance(home)
    if instance is None:
        log.info("another instance is running; opening it")
        open_existing(home)
        return

    sys.path.insert(0, str(base_dir()))
    try:
        from webgrab import server

        static = base_dir() / "webgrab" / "static"
        port = server.start(static, 8765)
    except Exception as exc:
        log.exception("server failed to start")
        fatal(f"本地服务启动失败：{exc}\n\n日志：{home / 'webgrab.log'}")
        return

    url = f"http://127.0.0.1:{port}/"
    (home / "url.txt").write_text(url, encoding="utf-8")
    log.info("server ready at %s", url)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    stop_event = threading.Event()

    def shutdown() -> None:
        if stop_event.is_set():
            return
        stop_event.set()
        try:
            server.stop()
        except Exception:
            pass

    run_tray(home, url, shutdown)
    log.info("running; close from tray or task manager")
    try:
        while not stop_event.is_set():
            stop_event.wait(3600)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
