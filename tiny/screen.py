"""Looking at the screen, only when the human has switched it on.

Screenshots are shrunk, sent to the brain, and never saved to disk.
"""

import subprocess

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage

MAX_WIDTH = 1280
# Tiny you never looks while these are in front: passwords, money, private keys.
BLOCKED_WORDS = ["1password", "bitwarden", "lastpass", "dashlane", "keychain", "keepass",
                 "bank", "wallet", "coinbase", "metamask", "ledger", "trezor", "paypal",
                 "venmo", "cash app", "robinhood", "fidelity", "schwab", "chase"]

NO_WINDOWS = "NO_WINDOWS"
_WALLPAPER_CHECK = (f" If the image shows only a desktop background with no app windows or content, "
                    f"reply with exactly: {NO_WINDOWS}")
PERMISSION_HELP = ("I can only see your wallpaper, not your windows. macOS needs Screen Recording "
                   "permission for the app that started me (Visual Studio Code, or Terminal): System "
                   "Settings → Privacy & Security → Screen Recording, switch it on, then restart me.")

PROMPT_HELP = ("Here's my screen right now. What am I working on, and how can you help? "
               "Be specific and brief." + _WALLPAPER_CHECK)
CHAT_NOTE = ("(Screen view is on: the attached image is my screen right now. Use it to answer."
             + _WALLPAPER_CHECK + ")")
PROMPT_WATCH = ("You're glancing at your human's screen while they work. If you notice something "
                "genuinely useful to say (a mistake, a shortcut, a deadline, a better approach), say "
                "it in one or two short sentences. If not, reply with exactly: NOTHING."
                + _WALLPAPER_CHECK)


def no_windows(reply: str) -> bool:
    return reply.strip().upper().rstrip(".") == NO_WINDOWS


class ScreenBlocked(Exception):
    pass


def frontmost_app() -> str:
    """Name of the app in front (macOS). Empty if it can't be read."""
    try:
        out = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first '
             'application process whose frontmost is true'],
            capture_output=True, text=True, timeout=3)
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def is_blocked(app_name: str) -> bool:
    name = app_name.lower()
    return any(word in name for word in BLOCKED_WORDS)


def capture() -> tuple[QImage, str]:
    """Grab the main screen, unless a sensitive app is in front."""
    app = frontmost_app()
    if is_blocked(app):
        raise ScreenBlocked(f"I don't look while {app} is open.")
    screen = QGuiApplication.primaryScreen()
    image = screen.grabWindow(0).toImage()
    if image.isNull() or image.width() == 0:
        raise ScreenBlocked(PERMISSION_HELP)
    if image.width() > MAX_WIDTH:
        image = image.scaledToWidth(MAX_WIDTH, Qt.SmoothTransformation)
    return image, app


def to_jpeg(image: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "JPEG", 70)
    return bytes(buf.data())


def changed(previous: QImage | None, current: QImage, threshold: float = 0.02) -> bool:
    """Rough check whether the screen changed enough to be worth another look."""
    if previous is None:
        return True
    a = previous.scaled(64, 40, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    b = current.scaled(64, 40, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    diff = sum(abs(a.pixelColor(x, y).lightness() - b.pixelColor(x, y).lightness())
               for x in range(64) for y in range(40))
    return diff / (64 * 40 * 255) > threshold
