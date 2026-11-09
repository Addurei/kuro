"""Natural voice: download (with fallback), safe unpacking, cute pitch, and the
Windows voice taking over whenever the natural one can't."""
import io
import tarfile
import time
import wave

import pytest

from app import voice as V


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


def test_hugging_face_first_then_github(local, monkeypatch):
    got = []

    def fetch(url, dest, min_bytes):
        got.append(url)
        if "huggingface" in url:
            raise RuntimeError("HTTP 403")
        buf = io.BytesIO()                           # a release archive, plus a sneaky path
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for name, data in (("en-us-amy-low.onnx", b"x" * 2000),
                               ("en-us-amy-low.onnx.json", b"{}"),
                               ("../evil.onnx", b"bad")):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
        dest.write_bytes(buf.getvalue())
    monkeypatch.setattr(V, "_fetch", fetch)
    V.download_voice("amy")
    onnx, cfg = V.voice_files("amy")
    assert onnx.read_bytes() == b"x" * 2000 and cfg.read_text() == "{}"
    assert "huggingface" in got[0] and "github.com" in got[-1]
    assert not (local / "Kuro" / "evil.onnx").exists()


def test_cute_pitch_is_a_higher_playback_rate():
    pcm = b"\x00\x01" * 16000
    plain = wave.open(io.BytesIO(V.wav_bytes(pcm, 22050)))
    cute = wave.open(io.BytesIO(V.wav_bytes(pcm, 22050, V.CUTE_PITCH)))
    assert plain.getframerate() == 22050 and cute.getframerate() == int(22050 * V.CUTE_PITCH)
    assert cute.getnframes() == plain.getnframes()   # same sound, played brighter


def test_speaker_uses_natural_voice_and_falls_back(local, monkeypatch):
    played, failed = [], {"now": False}

    class Natural:
        def __init__(self, name):
            self.name = name

        def synth(self, text, wpm=185, pitch=1.0):
            if failed["now"]:
                raise RuntimeError("broken model")
            return f"{self.name}:{text}:{pitch}".encode()
    monkeypatch.setattr(V, "download_voice", lambda name: None)
    monkeypatch.setattr(V, "NaturalVoice", Natural)
    monkeypatch.setattr(V, "play_wav", played.append)
    sp = V.Speaker(voice="amy", cute=True)
    for _ in range(50):
        if sp.engine == "natural":
            break
        time.sleep(0.02)
    assert sp.engine == "natural"
    sp.say("Hi Drey!")
    time.sleep(0.2)
    assert played == [f"amy:Hi Drey!:{V.CUTE_PITCH}".encode()]
    failed["now"] = True                              # model breaks: Windows voice takes over
    sp.say("Still here.")
    time.sleep(0.2)
    assert sp.engine == "windows" and len(played) == 1
    sp.stop()


def test_choosing_windows_voice_never_downloads(local, monkeypatch):
    monkeypatch.setattr(V, "download_voice", lambda n: pytest.fail("no download"))
    sp = V.Speaker(voice="windows")
    time.sleep(0.1)
    assert sp.engine == "windows"
    sp.stop()
