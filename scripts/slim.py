"""Shrink Kuro's libraries folder. Safe: test first, delete second.

run.bat calls this after installing or updating Kuro's libraries. It:
  1. uninstalls packages Kuro no longer uses (old voice / Word readers)
  2. moves the parts of Qt that Kuro never loads into a holding folder
     (Qt Designer, QML/Quick, PDF, SQL, translations, dev tools, ...)
  3. starts a hidden Qt window with what's left, draws, saves a JPEG
  4. only if that works deletes the holding folder; otherwise puts
     everything back exactly as it was

Usage:  python scripts/slim.py            (from Kuro's own venv)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# packages replaced by smaller code (see app/voice.py, app/tools.py)
UNUSED_PACKAGES = ["pyttsx3", "pypiwin32", "pywin32", "python-docx", "lxml"]

# Qt modules Kuro imports. Everything else in PySide6 is removable.
KEEP_MODULES = {"Core", "Gui", "Widgets", "DBus"}
# Qt library names (Qt6Xxx.dll / libQt6Xxx.so.6) that Core/Gui/Widgets and the
# Windows platform plugin rely on, plus a few that load lazily
KEEP_LIBS = {"Core", "Gui", "Widgets", "DBus", "XcbQpa", "WaylandClient", "EglFSDeviceIntegration",
             "OpenGL"}       # Qt6Gui links Qt6OpenGL-free on Windows; kept for safety (small)
REMOVE_DIRS = ["qml", "translations", "doc", "include", "typesystems", "glue", "scripts",
               "examples", "resources", "metatypes", "Qt/qml", "Qt/translations",
               "Qt/metatypes", "Qt/libexec", "Qt/resources"]
REMOVE_FILES = re.compile(
    r"^(opengl32sw\.dll|designer(\.exe)?|linguist(\.exe)?|assistant(\.exe)?|lrelease(\.exe)?|"
    r"lupdate(\.exe)?|qml\w*(\.exe)?|svgtoqml(\.exe)?|balsam\w*(\.exe)?|uic(\.exe)?|"
    r"rcc(\.exe)?|.*\.pyi|libpyside6qml.*|pyside6qml.*\.dll|Qt6?Designer.*|.*\.qmltypes)$",
    re.I)
# plugin folders Kuro needs (window system, images, widget styles)
KEEP_PLUGINS = {"platforms", "imageformats", "styles", "platformthemes", "xcbglintegrations",
                "wayland-shell-integration", "wayland-graphics-integration-client",
                "wayland-decoration-client", "platforminputcontexts"}


def _mb(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 1e6
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def removable(root: Path) -> list[Path]:
    out: list[Path] = []
    for d in REMOVE_DIRS:
        if (root / d).exists():
            out.append(root / d)
    for f in root.iterdir():
        if not f.is_file():
            continue
        name = f.name
        m = re.match(r"^Qt(\w+?)\.(pyd|abi3\.so|so)$", name)        # python bindings
        if m and m.group(1) not in KEEP_MODULES:
            out.append(f)
            continue
        m = re.match(r"^(?:lib)?Qt6(\w+?)\.(dll|so\.6.*)$", name)    # Qt libraries
        if m and m.group(1) not in KEEP_LIBS:
            out.append(f)
            continue
        if REMOVE_FILES.match(name):
            out.append(f)
    libdir = root / "Qt" / "lib"
    if libdir.is_dir():
        for f in libdir.iterdir():
            m = re.match(r"^libQt6(\w+?)\.so", f.name)
            if m and m.group(1) not in KEEP_LIBS:
                out.append(f)
    for plug in (root / "plugins", root / "Qt" / "plugins"):
        if plug.is_dir():
            out += [d for d in plug.iterdir() if d.is_dir() and d.name not in KEEP_PLUGINS]
            img = plug / "imageformats"
            if img.is_dir():                                        # svg/pdf/webp etc. unused
                out += [f for f in img.iterdir()
                        if not re.search(r"(jpeg|jpg|ico|gif)", f.name, re.I)]
    return out


CHECK = r"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen" if sys.platform != "win32" else "windows")
from PySide6.QtWidgets import QApplication, QWidget, QLabel
from PySide6.QtGui import QImage, QPainter, QColor, QPainterPath, QFont
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
app = QApplication([])
w = QLabel("ok"); w.resize(50, 20)
img = QImage(64, 64, QImage.Format_ARGB32); img.fill(QColor(10, 10, 10))
p = QPainter(img); path = QPainterPath(); path.addEllipse(5, 5, 50, 50)
p.setFont(QFont()); p.fillPath(path, QColor(180, 220, 90)); p.drawText(10, 30, "z"); p.end()
data = QByteArray(); buf = QBuffer(data); buf.open(QIODevice.WriteOnly)
assert img.save(buf, "JPG", 70) and len(data) > 100, "jpeg"
assert not QImage.fromData(bytes(data)).isNull(), "decode"
print("QT-OK")
"""


def qt_works() -> bool:
    try:
        r = subprocess.run([sys.executable, "-c", CHECK], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return "QT-OK" in r.stdout


def slim_qt() -> float:
    try:
        import PySide6
    except ImportError:
        return 0.0
    root = Path(PySide6.__file__).parent
    targets = removable(root)
    if not targets:
        return 0.0
    hold = root.parent / "_kuro_slim_hold"
    shutil.rmtree(hold, ignore_errors=True)
    hold.mkdir()
    moved: list[tuple[Path, Path]] = []
    freed = 0.0
    try:
        for i, src in enumerate(targets):
            if not src.exists():
                continue
            size = _mb(src)
            dst = hold / f"{i}_{src.name}"
            shutil.move(str(src), str(dst))
            moved.append((src, dst))
            freed += size
    except OSError as e:                      # a file in use: undo, try next time
        print("slim: couldn't move a file, skipping:", e)
        for src, dst in reversed(moved):
            shutil.move(str(dst), str(src))
        shutil.rmtree(hold, ignore_errors=True)
        return 0.0
    if qt_works():
        shutil.rmtree(hold, ignore_errors=True)
        return freed
    print("slim: Qt check failed, restoring everything")
    for src, dst in reversed(moved):
        shutil.move(str(dst), str(src))
    shutil.rmtree(hold, ignore_errors=True)
    return 0.0


def drop_packages() -> None:
    import importlib.metadata as md
    present = []
    for name in UNUSED_PACKAGES:
        try:
            md.version(name)
            present.append(name)
        except md.PackageNotFoundError:
            pass
    if present:
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "-q", *present],
                       check=False)
        print("slim: removed", ", ".join(present))


def main() -> int:
    drop_packages()
    freed = slim_qt()
    print(f"slim: freed {freed:.0f} MB of unused Qt parts" if freed else "slim: Qt already slim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
