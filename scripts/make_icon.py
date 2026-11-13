"""Render the app/tray icon from the cat rig.

Run:  python scripts/make_icon.py   (writes assets/icon.png and assets/icon.ico)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

from app.ui.cat_rig import Pose, draw_cat  # noqa: E402


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841
    size = 256
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    # zoom in on the head so the icon still reads at 16x16
    draw_cat(p, Pose(look_x=0.0, look_y=0.2, glow=0.0), QPointF(size / 2, size + 30), 2.25)
    p.end()
    out = ROOT / "assets"
    out.mkdir(exist_ok=True)
    img.save(str(out / "icon.png"))
    try:
        from PIL import Image
        Image.open(out / "icon.png").save(
            out / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                     (64, 64), (128, 128), (256, 256)])
    except ImportError:
        print("Pillow not installed: skipped icon.ico")
    print("Icon written to", out)


if __name__ == "__main__":
    main()
