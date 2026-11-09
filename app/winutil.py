"""Small Windows helpers: is a fullscreen app (game / video) in front?"""
from __future__ import annotations

import ctypes
import os
import platform

IS_WINDOWS = platform.system() == "Windows"
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def fullscreen_app() -> bool:
    """True when the foreground window covers its whole monitor (a game,
    a fullscreen video, a presentation). The desktop itself doesn't count."""
    if not IS_WINDOWS:
        return False
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value in SHELL_CLASSES:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False                         # that's Kuro himself

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return False
        m = mi.rcMonitor
        return (rect.left <= m.left and rect.top <= m.top
                and rect.right >= m.right and rect.bottom >= m.bottom)
    except Exception:
        return False


def foreground_window() -> int:
    if not IS_WINDOWS:
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow())  # type: ignore[attr-defined]
    except Exception:
        return 0


def focus_window(hwnd: int) -> None:
    if IS_WINDOWS and hwnd:
        try:
            ctypes.windll.user32.SetForegroundWindow(hwnd)  # type: ignore[attr-defined]
        except Exception:
            pass
