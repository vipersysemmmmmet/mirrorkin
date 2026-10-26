"""Tiny you talks out loud.

Uses Kokoro (an open-source neural voice that sounds human, runs offline) when its model is
in assets/voices/, and the Mac's built-in voices otherwise.
You talk to tiny you with macOS Dictation: press Fn twice in the chat box.
"""

import json
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from . import life

SETTINGS_FILE = life.DATA / "voice.json"
MODEL_DIR = Path(__file__).resolve().parent.parent / "assets" / "voices"
KOKORO_MODEL, KOKORO_VOICES = MODEL_DIR / "kokoro-v1.0.onnx", MODEL_DIR / "voices-v1.0.bin"

# Kokoro voices worth offering (af = American female, am = American male, bf/bm = British)
KOKORO = {"af_heart": "Heart", "af_bella": "Bella", "af_nicole": "Nicole (soft)", "af_sarah": "Sarah",
          "af_sky": "Sky", "bf_emma": "Emma (British)", "am_michael": "Michael", "am_adam": "Adam",
          "bm_george": "George (British)"}
# Natural-sounding Mac voices, matched on the first word (the Mac lists e.g. "Samantha (English (US))")
MAC = ["Samantha", "Flo", "Sandy", "Shelley", "Kathy", "Reed", "Eddy", "Daniel", "Tessa", "Tara"]
DEFAULT = {"on": False, "voice": "af_heart", "speed": 1.05, "rate": 190}

_lock = threading.Lock()
_player = None
_kokoro = None
_generation = 0


def settings() -> dict:
    if SETTINGS_FILE.exists():
        saved = {**DEFAULT, **json.loads(SETTINGS_FILE.read_text())}
        # Anyone still on the old robotic default moves to the human-sounding one
        if neural_available() and saved["voice"].split(" (")[0] == "Samantha":
            saved["voice"] = DEFAULT["voice"]
        return saved
    return dict(DEFAULT)


def save(values: dict) -> None:
    life.DATA.mkdir(exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(values, indent=2))


def neural_available() -> bool:
    if not (KOKORO_MODEL.exists() and KOKORO_VOICES.exists()):
        return False
    try:
        import kokoro_onnx  # noqa: F401
        return True
    except ImportError:
        return False


def mac_voices() -> list[str]:
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    names = {m.group(1) for m in re.finditer(r"^(.+?)\s+[a-z]{2,3}_[A-Z]{2}\b", out, re.MULTILINE)}
    found = {}
    for full in sorted(names, key=lambda n: "English (US)" not in n):  # prefer US accents
        found.setdefault(full.split(" (")[0], full)
    return [found[v] for v in MAC if v in found]


def choices() -> list[tuple[str, str]]:
    """(id, label) for the voice menu: human-sounding Kokoro voices first."""
    neural = list(KOKORO.items()) if neural_available() else []
    return neural + [(v, v.split(" (")[0] + " (robotic)") for v in mac_voices()]


def speakable(text: str) -> str:
    """What to read aloud: drop the sources list, links and emoji."""
    text = re.split(r"\n\s*Sources?:", text, maxsplit=1)[0]
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF☀-➿]", "", text)
    return " ".join(text.split())


def stop() -> None:
    global _player, _generation
    with _lock:
        _generation += 1  # cancels any voice still being generated
        if _player and _player.poll() is None:
            _player.terminate()
        _player = None


def _play(cmd: list[str], generation: int) -> None:
    global _player
    with _lock:
        if generation != _generation:
            return
        try:
            _player = subprocess.Popen(cmd)
        except OSError:
            _player = None


def _speak_neural(words: str, voice_id: str, speed: float, generation: int) -> None:
    global _kokoro
    import soundfile
    from kokoro_onnx import Kokoro
    if _kokoro is None:
        _kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    lang = "en-us" if voice_id.startswith("a") else "en-gb"
    samples, rate = _kokoro.create(words, voice=voice_id, speed=speed, lang=lang)
    wav = Path(tempfile.gettempdir()) / f"tiny_voice_{generation}.wav"
    soundfile.write(wav, samples, rate)
    _play(["afplay", str(wav)], generation)


def speak(text: str) -> None:
    """Say it out loud if voice is on. Interrupts anything still being said."""
    s = settings()
    words = speakable(text)
    if not s["on"] or not words:
        return
    stop()
    generation = _generation
    if s["voice"] in KOKORO and neural_available():
        # Generating takes a moment; never freeze tiny you while it happens
        threading.Thread(target=_speak_neural, args=(words, s["voice"], s["speed"], generation),
                         daemon=True).start()
    else:
        voice = s["voice"] if s["voice"] not in KOKORO else "Samantha"
        _play(["say", "-v", voice, "-r", str(s["rate"]), words], generation)
