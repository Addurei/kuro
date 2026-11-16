"""Render the character's face icon and installer art from the cat rig.

    python scripts/make_icon.py

For Kuro this writes assets/icon.png, assets/icon.ico and installer/art/.
For a custom character (a CHARACTER.json next to main.py) everything goes in
its private character/ folder instead: <icon>.png, <icon>.ico and
character/installer/, which installer/kuro.iss picks up with /DCatArt=...

The icon is the face on a soft rounded tile in the character's colours, so
it still reads at 16 px on a dark or a light taskbar.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QRadialGradient)

from app.character import CURRENT  # noqa: E402
from app.ui.cat_rig import Pose, draw_cat  # noqa: E402

MASTER = 1024
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
             (64, 64), (128, 128), (256, 256)]
SMALL_ART = (55, 64, 83, 110, 138)          # wizard corner image, 100%..250% DPI


def face(size: int = MASTER) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    m = size * 0.04
    tile = QPainterPath()
    tile.addRoundedRect(QRectF(m, m, size - 2 * m, size - 2 * m), size * 0.22, size * 0.22)
    light = sum(CURRENT["fur"]["LIT"][:3]) > 400
    g = QLinearGradient(0, 0, 0, size)
    if light:      # a light cat sits on a soft tint of its accent
        acc = QColor(*CURRENT["accent"][:3])
        top, bottom = QColor(acc).lighter(135), QColor(acc)
        top.setAlpha(255)
        g.setColorAt(0, QColor(min(255, top.red() + 20), min(255, top.green() + 30),
                               min(255, top.blue() + 30)))
        g.setColorAt(1, bottom)
    else:          # a dark cat sits on graphite, so its face and eyes glow
        g.setColorAt(0, QColor(52, 56, 68))
        g.setColorAt(1, QColor(18, 19, 24))
    p.fillPath(tile, g)
    glow = QRadialGradient(QPointF(size / 2, size * 0.56), size * 0.5)
    c0 = QColor(255, 255, 255, 120) if light else QColor(*CURRENT["accent"][:3], 70)
    c1 = QColor(c0)
    c1.setAlpha(0)
    glow.setColorAt(0, c0)
    glow.setColorAt(1, c1)
    p.save()
    p.setClipPath(tile)
    p.fillRect(img.rect(), glow)
    # zoomed so the head fills the tile and the body falls off the bottom
    draw_cat(p, Pose(t=1.0, yaw=0, look_y=0.15, ear=0.35), QPointF(size / 2, size * 1.14), size / 125)
    p.restore()
    p.setPen(QColor(255, 255, 255, 90 if light else 30))
    p.setBrush(Qt.NoBrush)
    p.drawPath(tile)
    p.end()
    return img


def main() -> None:
    from PIL import Image
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    custom = (ROOT / "CHARACTER.json").is_file()
    name = Path(CURRENT.get("icon") or "icon.png").stem
    out = ROOT / "character" if custom else ROOT / "assets"
    art = out / "installer" if custom else ROOT / "installer" / "art"
    out.mkdir(exist_ok=True)
    art.mkdir(parents=True, exist_ok=True)

    master = ROOT / "dist" / f"{name}-master.png"
    master.parent.mkdir(exist_ok=True)
    face().save(str(master))
    big = Image.open(master).convert("RGBA")
    big.resize((256, 256), Image.LANCZOS).save(out / f"{name}.png", optimize=True)
    big.save(out / f"{name}.ico", sizes=ICO_SIZES)
    for s in SMALL_ART:                       # BMP on white: the wizard's top band
        tile = Image.new("RGB", (s, s), (255, 255, 255))
        tile.paste(big.resize((s, s), Image.LANCZOS), (0, 0), big.resize((s, s), Image.LANCZOS))
        tile.save(art / f"small-{s}.bmp")
    print(f"{CURRENT['name']}: {out / (name + '.png')}, .ico and {art}")
    del app


if __name__ == "__main__":
    main()
