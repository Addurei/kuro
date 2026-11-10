"""Voice in and out.

Listening: push-to-talk. Press the voice hotkey, speak, and the cat
transcribes what you said with Whisper on Groq (free, with the same key as
his brain; understands English, Filipino and Taglish), or OpenAI's Whisper,
falling back to Google's free web speech service if neither is set up.

Speaking: a natural neural voice (Piper), running on this PC, offline. The
voice file (~60 MB) downloads once in the background; until it's there, or
if anything goes wrong, he uses the built-in Windows voice (SAPI).

Both are optional. If a package or microphone is missing, the cat keeps
working by text and tells you why voice is off.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

log = logging.getLogger("kuro")


# ------------------------------------------------------------ natural voices
VOICES = {
    # name: (label, huggingface path, github fallback archive)
    "amy": ("Amy", "en/en_US/amy/medium/en_US-amy-medium", "voice-en-us-amy-low.tar.gz"),
    "ryan": ("Ryan", "en/en_US/ryan/medium/en_US-ryan-medium", "voice-en-us-ryan-medium.tar.gz"),
    "lessac": ("Lessac", "en/en_US/lessac/medium/en_US-lessac-medium",
               "voice-en-us-lessac-medium.tar.gz"),
}
HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}{ext}"
GH = "https://github.com/rhasspy/piper/releases/download/v0.0.2/{archive}"
CUTE_PITCH = 1.12          # playback-rate lift: a touch higher and brighter, still clear


def voices_dir():
    import os
    from pathlib import Path
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    d = Path(base) / "Kuro" / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def voice_files(name: str):
    d = voices_dir()
    return d / f"{name}.onnx", d / f"{name}.onnx.json"


def _fetch(url: str, dest, min_bytes: int) -> None:
    import httpx
    part = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        with open(part, "wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
    if part.stat().st_size < min_bytes:
        part.unlink(missing_ok=True)
        raise RuntimeError("download too small")
    part.replace(dest)


def download_voice(name: str) -> None:
    """Get the voice once: Hugging Face first, the GitHub release as backup."""
    import json
    import tarfile
    import tempfile
    onnx, cfg = voice_files(name)
    if onnx.exists() and cfg.exists():
        return
    _label, path, archive = VOICES[name]
    try:
        _fetch(HF.format(path=path, ext=".onnx.json"), cfg, 500)
        _fetch(HF.format(path=path, ext=".onnx"), onnx, 1_000_000)
        json.loads(cfg.read_text(encoding="utf-8"))
        return
    except Exception as e:
        log.info("voice %s from Hugging Face failed (%s); trying GitHub", name, e)
    with tempfile.TemporaryDirectory() as tmp:
        from pathlib import Path
        arc = Path(tmp) / "voice.tar.gz"
        _fetch(GH.format(archive=archive), arc, 1_000_000)
        with tarfile.open(arc) as t:
            members = [m for m in t.getmembers()
                       if m.isfile() and m.name.endswith((".onnx", ".onnx.json"))
                       and "/" not in m.name.strip("./") and ".." not in m.name]
            for m in members:                       # only the two files, flat, no paths
                data = t.extractfile(m).read()
                (cfg if m.name.endswith(".json") else onnx).write_bytes(data)
    if not (onnx.exists() and cfg.exists()):
        raise RuntimeError("voice archive had no model")


def wav_bytes(chunks_pcm: bytes, rate: int, pitch: float = 1.0) -> bytes:
    """16-bit mono PCM -> a WAV file in memory. `pitch` > 1 plays it a little
    faster/higher (the cute voice) just by stating a higher sample rate."""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate * pitch))
        w.writeframes(chunks_pcm)
    return buf.getvalue()


class NaturalVoice:
    """Piper neural text-to-speech: load once, then each sentence is ~0.1-0.3 s."""

    def __init__(self, name: str):
        from piper import PiperVoice
        onnx, cfg = voice_files(name)
        self.voice = PiperVoice.load(str(onnx), config_path=str(cfg))
        self.rate = self.voice.config.sample_rate

    def synth(self, text: str, wpm: int = 185, pitch: float = 1.0) -> bytes:
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=max(0.6, min(1.6, 175 / max(80, wpm))))
        pcm = b"".join(c.audio_int16_bytes for c in self.voice.synthesize(text, cfg))
        return wav_bytes(pcm, self.rate, pitch)


ENV_STEP = 1 / 30       # one loudness reading per 33 ms: enough for a mouth


def envelope(wav: bytes, step: float = ENV_STEP) -> tuple[list[float], float]:
    """How loud a WAV is over time, 0..1 per `step` seconds, plus its length in
    seconds. This is what moves Kuro's mouth in time with his real voice."""
    import array
    import io
    import wave
    try:
        with wave.open(io.BytesIO(wav), "rb") as w:
            rate, width, chans = w.getframerate(), w.getsampwidth(), w.getnchannels()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return [], 0.0
    if width != 2 or not raw or rate <= 0:
        return [], 0.0
    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) // 2 * 2])
    if chans > 1:
        pcm = pcm[::chans]
    n = max(1, int(rate * step))
    out = []
    for i in range(0, len(pcm), n):
        chunk = pcm[i:i + n:4]                        # every 4th sample is plenty
        out.append((sum(v * v for v in chunk) / max(1, len(chunk))) ** 0.5)
    peak = max(out, default=0.0)
    if peak < 1:
        return [0.0] * len(out), len(pcm) / rate
    # a soft curve so quiet syllables still open his mouth a little
    return [min(1.0, (v / peak) ** 0.6) for v in out], len(pcm) / rate


def play_wav(data: bytes) -> None:
    import winsound                                   # standard library, Windows
    winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)


class Speaker:
    """Text-to-speech on its own thread so speaking never freezes the UI.
    Natural voice when ready, Windows voice as the always-there fallback."""

    def __init__(self, rate: int = 185, voice: str = "amy", cute: bool = True):
        self.available = False
        self.error = ""
        self.engine = "windows"
        self._q: queue.Queue[str | None] = queue.Queue()
        self._rate = rate
        self.voice_name, self.cute = voice, cute
        self._natural: NaturalVoice | None = None
        self._want_reload = False
        self._pending = 0                    # sentences queued or being spoken
        self._lock = threading.Lock()
        self._env: list[float] = []          # loudness of what's playing right now
        self._env_t0 = 0.0
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="tts").start()
        self._ready.wait(timeout=5)
        if voice in VOICES:
            threading.Thread(target=self._prepare, daemon=True, name="voice-dl").start()

    def set_voice(self, voice: str, cute: bool) -> None:
        """Settings changed: switch voice (downloading it first if needed)."""
        self.cute = cute
        if voice == self.voice_name:
            return
        self.voice_name, self._natural = voice, None
        self.engine = "windows"
        if voice in VOICES:
            threading.Thread(target=self._prepare, daemon=True, name="voice-dl").start()

    def _prepare(self) -> None:
        name = self.voice_name
        try:
            download_voice(name)
            voice = NaturalVoice(name)
        except Exception as e:
            log.info("natural voice %s unavailable, using the Windows voice: %s", name, e)
            return
        if name == self.voice_name:
            self._natural, self.engine = voice, "natural"
            self.available = True

    @staticmethod
    def sapi_rate(wpm: int) -> int:
        """SAPI speed is -10..10 with 0 ≈ 170 words/min, ~ +10 wpm per step."""
        return max(-10, min(10, round((wpm - 170) / 10)))

    def _run(self) -> None:
        sapi = None
        try:
            import comtypes
            import comtypes.client
            comtypes.CoInitialize()               # COM on this thread only
            sapi = comtypes.client.CreateObject("SAPI.SpVoice")
            sapi.Rate = self.sapi_rate(self._rate)
            voices = sapi.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                desc = v.GetDescription().lower()
                if "zira" in desc or "female" in desc:
                    sapi.Voice = v
                    break
            self.available = True
        except Exception as e:
            self.error = f"text-to-speech unavailable ({e})"
        self._ready.set()
        while True:
            text = self._q.get()
            if text is None:
                break
            try:
                self._speak(text, sapi)
            finally:
                self._env = []
                with self._lock:
                    self._pending = max(0, self._pending - 1)

    def _speak(self, text: str, sapi) -> None:
        natural = self._natural
        try:
            if natural is not None:
                wav = natural.synth(text, self._rate, CUTE_PITCH if self.cute else 1.0)
                env, _secs = envelope(wav)
                self._env_t0, self._env = time.monotonic(), env
                play_wav(wav)
                return
        except Exception as e:                    # broken voice file etc.: fall back
            log.info("natural voice failed, using Windows voice: %s", e)
            self._natural, self.engine = None, "windows"
        self._env = []
        try:
            if sapi is not None:
                sapi.Speak(text, 0)               # 0 = speak now, wait until done
        except Exception:
            pass

    def say(self, text: str) -> None:
        if text and (self.available or self._natural is not None):
            with self._lock:
                self._pending += 1
            self._q.put(text)

    @property
    def busy(self) -> bool:
        """Still has something to say (queued, being made, or playing)."""
        return self._pending > 0

    def level(self) -> float | None:
        """How loud his voice is at this instant, 0..1, for lip-sync. None when
        it isn't known (silent, or the Windows voice): the cat then mimes."""
        if not self.busy or self._natural is None:
            return None
        env = self._env
        if not env:
            return 0.0                            # between sentences: mouth closed
        i = int((time.monotonic() - self._env_t0) / ENV_STEP)
        return env[i] if 0 <= i < len(env) else 0.0

    def stop(self) -> None:
        self._q.put(None)

# Whisper sometimes "hears" these in near-silence; never act on them
PHANTOMS = {"", "you", "thank you", "thank you.", "thanks for watching!", "thanks for watching",
            "bye.", "bye", ".", "okay.", "salamat po.", "salamat."}
LANG_HINT = {"Filipino": "tl"}         # English / Taglish: let Whisper detect it
VOCAB = "Kuro, Spotify, YouTube, Blender, VS Code, Discord, Chrome, Opera GX, Unity."


def transcribe_whisper(wav: bytes, base: str, key: str, model: str,
                       language: str = "", timeout: float = 30.0) -> str:
    """Send one recording to an OpenAI-style /audio/transcriptions endpoint."""
    import httpx
    data = {"model": model, "response_format": "json", "temperature": "0", "prompt": VOCAB}
    if language:
        data["language"] = language
    r = httpx.post(base.rstrip("/") + "/audio/transcriptions", timeout=timeout,
                   headers={"Authorization": f"Bearer {key}"},
                   files={"file": ("speech.wav", wav, "audio/wav")}, data=data)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
    text = (r.json().get("text") or "").strip()
    return "" if text.lower().strip(" !") in PHANTOMS or text.lower() in PHANTOMS else text


class Listener:
    """Records one utterance from the microphone and transcribes it."""

    def __init__(self, settings=None):
        self.s = settings
        self.available = False
        self.error = ""
        self.last_engine = ""
        try:
            import speech_recognition as sr
            self._sr = sr
            self._rec = sr.Recognizer()
            self._rec.pause_threshold = 0.8
            with sr.Microphone():  # fails fast if no mic / no PyAudio
                pass
            self.available = True
        except Exception as e:
            self.error = f"microphone unavailable ({e})"

    def engines(self) -> list[tuple[str, str, str, str]]:
        """(name, base url, key, model), best first, for the keys you have."""
        s = self.s
        out = []
        if s is not None:
            if s.key_for("groq").strip():
                out.append(("Groq Whisper", "https://api.groq.com/openai/v1",
                            s.key_for("groq").strip(), "whisper-large-v3-turbo"))
            if s.key_for("openai").strip():
                out.append(("OpenAI Whisper", "https://api.openai.com/v1",
                            s.key_for("openai").strip(), "whisper-1"))
        return out

    def listen_once(self, timeout: float = 6, phrase_limit: float = 15) -> str:
        """Blocking. Returns the transcript, or '' if nothing was understood."""
        if not self.available:
            raise RuntimeError(self.error)
        sr = self._sr
        with sr.Microphone() as source:
            self._rec.adjust_for_ambient_noise(source, duration=0.3)
            try:
                audio = self._rec.listen(source, timeout=timeout,
                                         phrase_time_limit=phrase_limit)
            except sr.WaitTimeoutError:
                return ""
        return self.transcribe(audio)

    def transcribe(self, audio) -> str:
        lang = LANG_HINT.get(getattr(self.s, "language", "English"), "")
        wav = None
        problems = []
        for name, base, key, model in self.engines():
            try:
                if wav is None:
                    # 16 kHz mono is what Whisper uses anyway: smaller upload
                    wav = audio.get_wav_data(convert_rate=16000, convert_width=2)
                text = transcribe_whisper(wav, base, key, model, lang)
                self.last_engine = name
                return text
            except Exception as e:                   # out of quota, offline...: next
                problems.append(f"{name}: {e}")
        sr = self._sr
        try:
            self.last_engine = "Google"
            return self._rec.recognize_google(
                audio, language="fil-PH" if lang == "tl" else "en-US")
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            problems.append(f"Google: {e}")
            raise RuntimeError("speech service unreachable (" + "; ".join(problems)[:200] + ")")


WAKE_WORDS = ("kuro", "kurro", "curo", "kuru", "cuero", "kudo", "kiro", "cuddo", "kura")


def find_wake(text: str) -> tuple[bool, str]:
    """('hey kuro, open spotify') -> (True, 'open spotify')."""
    words = text.lower().replace(",", " ").replace(".", " ").split()
    for i, w in enumerate(words):
        if w in WAKE_WORDS or (w.startswith("kur") and len(w) <= 5):
            rest = " ".join(text.replace(",", " ").split()[i + 1:]).strip(" ,.!?")
            return True, rest
    return False, ""


class WakeWord:
    """Hands-free 'Hey Kuro'. Listens in the background for short phrases;
    only phrases loud enough to be speech are sent to the speech service,
    and only to check for his name. Paused while Kuro talks or listens."""

    def __init__(self, on_wake, max_phrase: float = 4.0):
        self.on_wake = on_wake            # called with (command_text or "")
        self.max_phrase = max_phrase
        self.paused = False
        self._stop = False
        self.error = ""
        self._thread = None

    def start(self) -> bool:
        try:
            import speech_recognition as sr
            self._sr = sr
            self._rec = sr.Recognizer()
            self._rec.dynamic_energy_threshold = True
            self._rec.pause_threshold = 0.6
            with sr.Microphone():
                pass
        except Exception as e:
            self.error = f"microphone unavailable ({e})"
            return False
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="wake")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop = True

    def _run(self) -> None:
        sr = self._sr
        import time
        while not self._stop:
            if self.paused:
                time.sleep(0.3)
                continue
            try:
                with sr.Microphone() as source:
                    self._rec.adjust_for_ambient_noise(source, duration=0.4)
                    while not self._stop and not self.paused:
                        try:
                            audio = self._rec.listen(source, timeout=5,
                                                     phrase_time_limit=self.max_phrase)
                        except sr.WaitTimeoutError:
                            continue
                        if self.paused or self._stop:
                            break
                        try:
                            text = self._rec.recognize_google(audio)
                        except (sr.UnknownValueError, sr.RequestError):
                            continue
                        hit, rest = find_wake(text)
                        if hit:
                            self.paused = True     # controller resumes when done
                            self.on_wake(rest)
                            break
            except Exception as e:                 # mic unplugged etc.
                self.error = str(e)
                time.sleep(2)
