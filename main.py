"""Start Kuro.   python main.py

main.py is a tiny supervisor: it runs Kuro in a child process and, if he
ever crashes, writes what happened to %APPDATA%\\Kuro\\crash.log and brings
him straight back. Quitting normally (right-click → Quit) ends both.
If he crashes over and over (4 times in 2 minutes) it stops and says so,
instead of looping forever.
"""
import faulthandler
import gc
import os
import sys as _sys
from pathlib import Path as _Path

# A staged update is applied before ANY of the app is imported, so the whole
# program runs as one version. See app/updater.py for the full story.
if "--child" not in _sys.argv:
    try:
        _data = _Path(os.environ.get("APPDATA", str(_Path.home() / ".config"))) / "Kuro"
        if (_data / "update" / "pending" / "UPDATE_VERSION").is_file():
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "kuro_updater", _Path(__file__).resolve().parent / "app" / "updater.py")
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _mod.apply_pending(_Path(__file__).resolve().parent, _data)
    except Exception:
        pass
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

CHILD_FLAG, RECOVERED_FLAG = "--child", "--recovered"
MAX_CRASHES, WINDOW_S = 4, 120


def _data_dir():
    from app.config import data_dir
    return data_dir()


def _crash_log():
    path = _data_dir() / "crash.log"
    try:
        if path.exists() and path.stat().st_size > 200_000:      # keep it small
            path.write_bytes(path.read_bytes()[-100_000:])
    except OSError:
        pass
    return path


def _note(text: str) -> None:
    try:
        with open(_crash_log(), "a", encoding="utf-8") as f:
            f.write(f"\n==== {datetime.now():%Y-%m-%d %H:%M:%S} ====\n{text.rstrip()}\n")
    except OSError:
        pass


# --------------------------------------------------------------------- child
def run_kuro(recovered: bool) -> int:
    from PySide6.QtCore import QLockFile, Qt, QTimer
    from PySide6.QtWidgets import QApplication

    from app.config import data_dir, load_settings, setup_logging
    from app.controller import Controller

    # native crashes (segfaults in Qt / drivers): Python dumps every thread's
    # stack into the crash log before the process dies
    fault_file = open(_crash_log(), "a", encoding="utf-8")
    faulthandler.enable(fault_file, all_threads=True)

    setup_logging()

    def hook(kind, exc, value, tb):
        text = "".join(traceback.format_exception(exc, value, tb))
        _note(f"{kind} error (Kuro kept running):\n{text}")
    # an error inside a button handler or timer shouldn't take him down
    sys.excepthook = lambda e, v, tb: hook("UI", e, v, tb)
    threading.excepthook = lambda a: hook(f"thread '{a.thread.name if a.thread else '?'}'",
                                          a.exc_type, a.exc_value, a.exc_traceback)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([a for a in sys.argv if a not in (CHILD_FLAG, RECOVERED_FLAG)])
    app.setApplicationName("Kuro")
    app.setQuitOnLastWindowClosed(False)  # closing the chat must not quit the cat

    # Only one cat at a time.
    lock = QLockFile(str(data_dir() / "kuro.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print("Kuro is already running.")
        return 0

    # Python's garbage collector runs on whichever thread happens to allocate
    # when it's due, and frees what it finds right there. If that's a
    # background thread (the brain, the music watcher) and the garbage holds a
    # Qt or COM object, freeing it there crashes the whole app (this was the
    # "access violation ... Garbage-collecting" crash). So collection only
    # ever happens here, on the UI thread, a few times a second, in tiny bites.
    gc.disable()
    gc_timer = QTimer(interval=400)
    gc_ticks = [0]

    def collect() -> None:
        gc_ticks[0] += 1
        gc.collect(2 if gc_ticks[0] % 150 == 0 else 0)          # full sweep each minute
    gc_timer.timeout.connect(collect)
    gc_timer.start()

    controller = Controller(app, load_settings())
    from app import housekeeping
    housekeeping.start(controller.s)
    if recovered:
        from app.i18n import t
        QTimer.singleShot(1800, lambda: controller.hud.say(
            t(controller.s, "recovered"), seconds=5))
    return app.exec()


# ---------------------------------------------------------------- supervisor
def _message_box(text: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "Kuro", 0x30)  # type: ignore[attr-defined]
            return
        except Exception:
            pass
    print(text)


def _child_cmd(recovered: bool) -> list[str]:
    if getattr(sys, "frozen", False):                  # packaged Kuro.exe
        cmd = [sys.executable, CHILD_FLAG]
    else:
        cmd = [sys.executable, os.path.abspath(__file__), CHILD_FLAG]
    return cmd + ([RECOVERED_FLAG] if recovered else [])


def supervise() -> int:
    crashes: list[float] = []
    recovered = False
    while True:
        started = time.monotonic()
        try:
            code = subprocess.call(_child_cmd(recovered))
        except KeyboardInterrupt:
            return 0
        if code == 0:
            return 0                                   # quit normally / already running
        now = time.monotonic()
        crashes = [t for t in crashes if now - t < WINDOW_S] + [now]
        _note(f"Kuro stopped unexpectedly (exit code {code}) after "
              f"{now - started:.0f}s. Restarting ({len(crashes)}/{MAX_CRASHES}).")
        if len(crashes) >= MAX_CRASHES:
            _note("Too many crashes in a row; not restarting.")
            _message_box("Kuro keeps crashing, so he's resting for now.\n\n"
                         f"The details are in:\n{_crash_log()}\n\n"
                         "Start him again with run.bat, or send that file to the developer.")
            return code
        recovered = True
        time.sleep(1.5)


def main() -> int:
    if CHILD_FLAG in sys.argv or os.environ.get("KURO_NO_SUPERVISOR"):
        return run_kuro(recovered=RECOVERED_FLAG in sys.argv)
    return supervise()


if __name__ == "__main__":
    sys.exit(main())
