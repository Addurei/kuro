"""Voice in and out.

Listening: push-to-talk. Press the voice hotkey, speak, and the cat
transcribes what you said (Google's free speech API via the
SpeechRecognition package, so it needs internet).

Speaking: pyttsx3, which uses the built-in Windows voices (works offline).

Both are optional. If a package or microphone is missing, the cat keeps
working by text and tells you why voice is off.
"""
from __future__ import annotations

import queue
import threading


class Speaker:
    """Text-to-speech on its own thread so speaking never freezes the UI."""

    def __init__(self, rate: int = 185):
        self.available = False
        self.error = ""
        self._q: queue.Queue[str | None] = queue.Queue()
        self._rate = rate
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="tts").start()
        self._ready.wait(timeout=5)

    def _run(self) -> None:
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", self._rate)
            # Prefer a female voice if one is installed (Windows "Zira").
            for v in engine.getProperty("voices"):
                if "zira" in v.name.lower() or "female" in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break
            self.available = True
        except Exception as e:
            self.error = f"text-to-speech unavailable ({e})"
            self._ready.set()
            return
        self._ready.set()
        while True:
            text = self._q.get()
            if text is None:
                break
            try:
                engine.say(text)
                engine.runAndWait()
            except Exception:
                pass

    def say(self, text: str) -> None:
        if self.available and text:
            self._q.put(text)

    def stop(self) -> None:
        self._q.put(None)


class Listener:
    """Records one utterance from the microphone and transcribes it."""

    def __init__(self):
        self.available = False
        self.error = ""
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
        try:
            return self._rec.recognize_google(audio)
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as e:
            raise RuntimeError(f"speech service unreachable ({e})")


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
