"""生成示例页面用的图片、视频与 PWA 图标。

只依赖 Pillow；视频优先用 ffmpeg（third_party 内置 / PATH / 环境变量），
没有 ffmpeg 时退化为最小占位 mp4 并打印中文提示。

用法：
    .venv\\Scripts\\python.exe tools\\make_assets.py            # 缺什么补什么（幂等）
    .venv\\Scripts\\python.exe tools\\make_assets.py --force    # 全部重做
    .venv\\Scripts\\python.exe tools\\make_assets.py --only icons
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover - 提示安装
    print("缺少 Pillow：.venv\\Scripts\\python.exe -m pip install pillow", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "core" / "webgrab" / "static"
DEMO = STATIC / "demo"
ICONS = STATIC / "icons"

IMG_SIZE = (900, 1200)
COVER_SIZE = (1280, 720)
VIDEO_SECONDS = 6
VIDEO_FPS = 30

SERIES = [
    # (文件名, 天空上色, 天空下色, 海水色, 太阳色, 太阳位置比例, 地平线比例)
    ("a1.jpg", (24, 30, 62), (232, 138, 96), (18, 34, 58), (255, 214, 150), (0.62, 0.44), 0.56),
    ("a2.jpg", (16, 22, 48), (196, 108, 120), (14, 26, 48), (255, 186, 132), (0.30, 0.40), 0.52),
    ("a3.jpg", (12, 18, 40), (120, 138, 190), (12, 24, 44), (232, 238, 255), (0.74, 0.36), 0.60),
    ("a4.jpg", (28, 22, 44), (244, 168, 106), (16, 28, 46), (255, 226, 176), (0.46, 0.50), 0.48),
]


# ----------------------------------------------------------------- 基础绘制


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _mix(c1, c2, t: float):
    t = max(0.0, min(1.0, t))
    return tuple(_lerp(c1[i], c2[i], t) for i in range(3))


def vertical_gradient(size, top, bottom, mid=None, mid_pos=0.5) -> Image.Image:
    """一像素宽渐变再拉伸，比逐像素快很多。"""
    width, height = size
    column = Image.new("RGB", (1, height))
    pixels = column.load()
    for y in range(height):
        t = y / max(1, height - 1)
        if mid is None:
            color = _mix(top, bottom, t)
        elif t < mid_pos:
            color = _mix(top, mid, t / max(1e-6, mid_pos))
        else:
            color = _mix(mid, bottom, (t - mid_pos) / max(1e-6, 1 - mid_pos))
        pixels[0, y] = color
    return column.resize((width, height), Image.BILINEAR)


def radial_glow(size, center, radius, color, strength=255) -> Image.Image:
    """小图算圆滑径向渐变再放大，避免出现一圈圈的硬边。"""
    width, height = size
    small = 128
    layer = Image.new("L", (small, small), 0)
    pixels = layer.load()
    cx, cy = center
    for y in range(small):
        ys = (y + 0.5) / small * height
        for x in range(small):
            xs = (x + 0.5) / small * width
            d = math.hypot(xs - cx, ys - cy) / max(1.0, radius)
            if d >= 1.0:
                continue
            pixels[x, y] = int(strength * (1.0 - d) ** 2.0)
    layer = layer.resize((width, height), Image.BICUBIC)
    glow = Image.new("RGB", size, color)
    glow.putalpha(layer)
    return glow


def _wave(draw, x0, x1, y, amp, freq, phase, color, width):
    points = []
    step = 6
    for x in range(int(x0), int(x1) + step, step):
        yy = y + amp * math.sin((x / max(1, x1 - x0)) * math.tau * freq + phase)
        points.append((x, yy))
    if len(points) > 1:
        draw.line(points, fill=color, width=width, joint="curve")


def paint_scene(size, palette) -> Image.Image:
    name, top, bottom, sea, sun_color, sun_pos, horizon = palette
    width, height = size
    horizon_y = int(height * horizon)

    sky = vertical_gradient(
        (width, horizon_y + 1),
        _mix(top, (6, 8, 14), 0.25),
        bottom,
        mid=_mix(top, bottom, 0.55),
        mid_pos=0.62,
    )
    sea_h = height - horizon_y
    sea_img = vertical_gradient(
        (width, sea_h),
        _mix(sea, bottom, 0.42),
        _mix(sea, (2, 3, 6), 0.6),
        mid=_mix(sea, bottom, 0.12),
        mid_pos=0.35,
    )
    img = Image.new("RGB", (width, height))
    img.paste(sky, (0, 0))
    img.paste(sea_img, (0, horizon_y))

    # 太阳 + 光晕
    cx = int(width * sun_pos[0])
    cy = int(height * sun_pos[1])
    sun_r = int(min(width, height) * 0.055)
    glow = radial_glow((width, height), (cx, cy), int(width * 0.46), sun_color, strength=210)
    halo = Image.composite(
        Image.new("RGB", (width, height), sun_color),
        Image.new("RGB", (width, height), (0, 0, 0)),
        glow.getchannel("A"),
    )
    img = ImageChops.add(img, halo)
    draw = ImageDraw.Draw(img, "RGBA")
    draw.ellipse([cx - sun_r * 1.7, cy - sun_r * 1.7, cx + sun_r * 1.7, cy + sun_r * 1.7],
                 fill=(*sun_color, 30))
    draw.ellipse([cx - sun_r, cy - sun_r, cx + sun_r, cy + sun_r], fill=(*sun_color, 255))

    # 海面反光带
    band = Image.new("RGBA", (width, sea_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for i in range(28):
        t = i / 28
        y = horizon_y + int(sea_h * t * 0.9)
        half = int(width * 0.05 + width * 0.26 * t)
        alpha = int(120 * (1 - t) ** 2)
        if alpha <= 0:
            continue
        bd.rectangle([cx - half, y, cx + half, y + 2 + int(3 * t)], fill=(*sun_color, alpha))
    band = band.filter(ImageFilter.GaussianBlur(3))
    img.paste(band, (0, horizon_y), band)

    draw = ImageDraw.Draw(img, "RGBA")

    # 海平线 + 波纹
    draw.line([(0, horizon_y), (width, horizon_y)], fill=(*sun_color, 90), width=2)
    for i in range(9):
        t = i / 9
        y = horizon_y + int(sea_h * (0.08 + 0.8 * t))
        amp = 2 + 7 * t
        _wave(draw, -20, width + 20, y, amp, 1.6 + i * 0.4, i * 1.7,
              (*sun_color, int(38 * (1 - t))), 1 + int(t * 2))

    # 远处小岛剪影
    island_y = horizon_y + 2
    island = [
        (int(width * 0.02), island_y),
        (int(width * 0.12), island_y - int(height * 0.028)),
        (int(width * 0.22), island_y - int(height * 0.012)),
        (int(width * 0.34), island_y - int(height * 0.04)),
        (int(width * 0.48), island_y),
    ]
    draw.polygon(island, fill=(6, 10, 18, 210))

    # 前景礁石
    rock = [
        (0, height),
        (0, int(height * 0.86)),
        (int(width * 0.10), int(height * 0.90)),
        (int(width * 0.17), int(height * 0.97)),
        (int(width * 0.30), height),
    ]
    draw.polygon(rock, fill=(4, 6, 12, 235))

    # 暗角
    vignette = Image.new("L", (width, height), 0)
    vd = ImageDraw.Draw(vignette)
    pad = int(min(width, height) * 0.30)
    vd.ellipse([-pad, -pad, width + pad, height + pad], fill=90)
    mask = Image.eval(vignette.filter(ImageFilter.GaussianBlur(width * 0.12)),
                      lambda v: int(70 - v * 0.7) if v < 100 else 0)
    img = Image.composite(Image.new("RGB", (width, height), (2, 3, 6)), img, mask)

    # 细颗粒，压掉渐变的色带
    grain = Image.effect_noise((width, height), 24).convert("RGB")
    grain = grain.point(lambda v: 128 + (v - 128) // 7)
    img = ImageChops.add(img, grain, scale=1.0, offset=-128)

    return img


def save_jpeg(img: Image.Image, path: Path, quality: int = 84) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    print(f"  写入 {path.relative_to(ROOT)} · {path.stat().st_size // 1024}KB")


# ------------------------------------------------------------------- 图标


def icon_image(size: int, padding_ratio: float = 0.0, radius_ratio: float = 0.22) -> Image.Image:
    """渐变圆角方块 + 下载箭头；padding_ratio 用于 maskable 的安全区。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    radius = int(size * radius_ratio)
    plate = vertical_gradient((size, size), (92, 108, 255), (34, 211, 238),
                              mid=(120, 92, 240), mid_pos=0.55).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.paste(plate, (0, 0), mask)

    draw = ImageDraw.Draw(img, "RGBA")
    inner = int(size * (1 - 2 * padding_ratio))
    off = (size - inner) // 2
    cx = size // 2

    # 托盘
    tray_top = off + int(inner * 0.62)
    tray_bottom = off + int(inner * 0.80)
    half = int(inner * 0.26)
    thick = max(3, int(inner * 0.075))
    draw.line([(cx - half, tray_top), (cx - half, tray_bottom)], fill=(255, 255, 255, 235), width=thick)
    draw.line([(cx + half, tray_top), (cx + half, tray_bottom)], fill=(255, 255, 255, 235), width=thick)
    draw.line([(cx - half - thick // 2, tray_bottom), (cx + half + thick // 2, tray_bottom)],
              fill=(255, 255, 255, 235), width=thick)

    # 向下箭头
    arrow_top = off + int(inner * 0.20)
    arrow_bottom = off + int(inner * 0.56)
    draw.line([(cx, arrow_top), (cx, arrow_bottom)], fill=(255, 255, 255, 245), width=thick)
    head = int(inner * 0.14)
    draw.line([(cx - head, arrow_bottom - head), (cx, arrow_bottom)], fill=(255, 255, 255, 245), width=thick)
    draw.line([(cx + head, arrow_bottom - head), (cx, arrow_bottom)], fill=(255, 255, 255, 245), width=thick)

    return img


# ----------------------------------------------------------------- 视频


def find_ffmpeg() -> str:
    candidates = []
    env_file = os.environ.get("WEBGRAB_FFMPEG", "").strip()
    if env_file:
        candidates.append(Path(env_file))
    env_dir = os.environ.get("WEBGRAB_FFMPEG_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir) / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
    candidates.append(ROOT / "third_party" / "ffmpeg" / "bin" / "ffmpeg.exe")
    candidates.append(ROOT / "third_party" / "ffmpeg" / "bin" / "ffmpeg")
    for path in candidates:
        if path.is_file():
            return str(path)
    found = shutil.which("ffmpeg")
    return found or ""


def _placeholder_mp4(path: Path) -> None:
    """没有 ffmpeg 时的最小占位文件：能识别成 mp4 容器，但不可播放。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    ftyp = struct.pack(">I", 24) + b"ftypisom" + struct.pack(">I", 512) + b"isomiso2avc1mp41"
    free = struct.pack(">I", 8) + b"free"
    data = ftyp + free
    path.write_bytes(data)
    print(f"  写入占位 {path.relative_to(ROOT)}（{len(data)} 字节，不可播放）")


def make_video(force: bool = False) -> str:
    out = DEMO / "clip.mp4"
    if out.exists() and out.stat().st_size > 16 * 1024 and not force:
        print(f"  跳过 {out.relative_to(ROOT)}（已存在）")
        return "skip"

    ffmpeg = find_ffmpeg()
    cover = DEMO / "cover.jpg"
    if not ffmpeg:
        print("  找不到 ffmpeg：mp4 用占位素材代替。")
        print("  想生成真视频：把 ffmpeg.exe 放到 third_party\\ffmpeg\\bin\\，或设 WEBGRAB_FFMPEG。")
        _placeholder_mp4(out)
        return "placeholder"

    if not cover.is_file():
        print("  缺少 cover.jpg，先做图片。")
        return "fail"

    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-loop", "1", "-framerate", str(VIDEO_FPS), "-i", str(cover),
        "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:duration={VIDEO_SECONDS}",
        "-t", str(VIDEO_SECONDS),
        "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
               "fps=30,format=yuv420p,fade=t=in:st=0:d=0.5,fade=t=out:st=5.4:d=0.6",
        "-af", "volume=0.10,afade=t=in:st=0:d=0.5,afade=t=out:st=5.2:d=0.8",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
        "-profile:v", "main", "-level", "3.1",
        "-c:a", "aac", "-b:a", "96k", "-ac", "2", "-ar", "44100",
        "-shortest", "-movflags", "+faststart",
        str(out),
    ]
    print("  ffmpeg 生成 6 秒 mp4（1280×720 · H.264 + AAC）…")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  ffmpeg 调用失败：{exc}")
        _placeholder_mp4(out)
        return "placeholder"
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 4096:
        detail = (proc.stderr or "").strip().splitlines()
        print("  ffmpeg 出错：" + (detail[-1] if detail else f"退出码 {proc.returncode}"))
        _placeholder_mp4(out)
        return "fail"
    print(f"  写入 {out.relative_to(ROOT)} · {out.stat().st_size // 1024}KB")
    return "ok"


# ------------------------------------------------------------------- 入口


def make_images(force: bool = False) -> None:
    print("示例图片：")
    for palette in SERIES:
        out = DEMO / palette[0]
        if out.exists() and out.stat().st_size > 8 * 1024 and not force:
            print(f"  跳过 {out.relative_to(ROOT)}（已存在）")
            continue
        img = paint_scene(IMG_SIZE, palette)
        save_jpeg(img, out)

    cover = DEMO / "cover.jpg"
    if cover.exists() and cover.stat().st_size > 8 * 1024 and not force:
        print(f"  跳过 {cover.relative_to(ROOT)}（已存在）")
    else:
        save_jpeg(paint_scene(COVER_SIZE, SERIES[0]), cover, quality=86)


def make_icons(force: bool = False) -> None:
    print("PWA 图标：")
    targets = [
        ("icon-192.png", 192, 0.0),
        ("icon-512.png", 512, 0.0),
        ("maskable-512.png", 512, 0.12),
    ]
    for name, size, pad in targets:
        out = ICONS / name
        if out.exists() and out.stat().st_size > 1024 and not force:
            print(f"  跳过 {out.relative_to(ROOT)}（已存在）")
            continue
        img = icon_image(size, padding_ratio=pad)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG", optimize=True)
        print(f"  写入 {out.relative_to(ROOT)} · {out.stat().st_size // 1024}KB")

    ico = STATIC / "favicon.ico"
    if ico.exists() and ico.stat().st_size > 512 and not force:
        print(f"  跳过 {ico.relative_to(ROOT)}（已存在）")
    else:
        base = icon_image(64)
        ico.parent.mkdir(parents=True, exist_ok=True)
        base.save(ico, "ICO", sizes=[(16, 16), (32, 32), (48, 48)])
        print(f"  写入 {ico.relative_to(ROOT)} · {ico.stat().st_size // 1024}KB")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 WebGrab 示例素材与图标")
    parser.add_argument("--force", action="store_true", help="全部重做（默认跳过已存在的文件）")
    parser.add_argument("--only", choices=["all", "images", "video", "icons"], default="all")
    args = parser.parse_args(argv)

    if args.only in ("all", "images"):
        make_images(args.force)
    if args.only in ("all", "icons"):
        make_icons(args.force)
    if args.only in ("all", "video"):
        print("示例视频：")
        make_video(args.force)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
