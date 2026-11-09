"""Start Kuro.   python main.py"""
import sys

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtWidgets import QApplication

from app.config import data_dir, load_settings, setup_logging
from app.controller import Controller


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Kuro")
    app.setQuitOnLastWindowClosed(False)  # closing the chat must not quit the cat

    # Only one cat at a time.
    lock = QLockFile(str(data_dir() / "kuro.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print("Kuro is already running.")
        return 0

    controller = Controller(app, load_settings())  # noqa: F841 (keep alive)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
