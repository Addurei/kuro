"""Startup fixes: no console window, GC only on the UI thread, storage trims."""
from pathlib import Path
from types import SimpleNamespace

from app import housekeeping as H

ROOT = Path(__file__).resolve().parent.parent


def test_run_bat_starts_kuro_without_a_console():
    bat = (ROOT / "run.bat").read_text()
    assert 'start "" "%PYW%" main.py' in bat              # pythonw: no black window
    assert '"%PY%" main.py' not in bat
    assert "/min" in bat and "pause" not in bat


def test_garbage_collection_stays_on_the_ui_thread():
    src = (ROOT / "main.py").read_text()
    assert "gc.disable()" in src and "gc.collect(" in src


def test_unused_voices_go_the_chosen_one_stays(tmp_path):
    for f in ("amy.onnx", "amy.onnx.json", "ryan.onnx", "ryan.onnx.json", "lessac.onnx.part",
              "notes.txt"):
        (tmp_path / f).write_text("x")
    gone = {p.name for p in H.unused_voices(tmp_path, "amy")}
    assert gone == {"ryan.onnx", "ryan.onnx.json", "lessac.onnx.part"}


def test_speech_extras_keep_the_windows_flac(tmp_path):
    sr = tmp_path / "speech_recognition"
    (sr / "pocketsphinx-data" / "en").mkdir(parents=True)
    for f in ("__init__.py", "flac-win32.exe", "flac-mac", "flac-linux-x86", "flac-linux-x86_64"):
        (sr / f).write_text("x")
    names = {p.name for p in H.speech_extras(tmp_path, "win32")}
    assert names == {"pocketsphinx-data", "flac-mac", "flac-linux-x86", "flac-linux-x86_64"}


def test_tidy_frees_space_and_keeps_what_matters(tmp_path):
    voices, site = tmp_path / "voices", tmp_path / "site"
    voices.mkdir()
    (voices / "ryan.onnx").write_bytes(b"0" * 5000)
    (voices / "amy.onnx").write_bytes(b"0" * 5000)
    (site / "speech_recognition" / "pocketsphinx-data").mkdir(parents=True)
    (site / "speech_recognition" / "pocketsphinx-data" / "m.bin").write_bytes(b"0" * 9000)
    (site / "speech_recognition" / "__init__.py").write_text("")
    freed = H.tidy(SimpleNamespace(voice="amy"), voices, site)
    assert freed >= 14000
    assert (voices / "amy.onnx").exists() and not (voices / "ryan.onnx").exists()
    assert (site / "speech_recognition" / "__init__.py").exists()
    assert H.tidy(SimpleNamespace(voice="amy"), voices, site) == 0      # nothing left to do
