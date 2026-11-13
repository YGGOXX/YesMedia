"""Generate build/app.ico for the Windows executable."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "app.ico"
SRC = ROOT / "core" / "webgrab" / "static" / "icons" / "icon-192.png"


def fallback() -> Image.Image:
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, size - 8, size - 8), radius=64, fill=(88, 120, 255, 255))
    d.rounded_rectangle((8, 8, size - 8, size - 8), radius=64, outline=(150, 180, 255, 255), width=6)
    d.line((size // 2, 52, size // 2, 160), fill=(255, 255, 255, 255), width=26)
    d.polygon([(72, 132), (184, 132), (128, 206)], fill=(255, 255, 255, 255))
    return img


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(SRC).convert("RGBA") if SRC.exists() else fallback()
    img = img.resize((256, 256), Image.LANCZOS)
    img.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
