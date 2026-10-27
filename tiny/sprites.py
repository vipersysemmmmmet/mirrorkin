"""Realistic tiny you: frames rendered in Blender (avatar/build_tiny.py), played on the desktop."""

import json
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPixmap

from . import life

ROOT = Path(__file__).resolve().parent.parent
AVATAR_DIR = life.DATA / "avatar"
SPRITE_DIR = AVATAR_DIR / "sprites"
WALK_FRAMES = 8
BLENDER = os.environ.get("BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")

def available() -> bool:
    return (SPRITE_DIR / "idle.png").exists()


class SpriteSet:
    """All frames cropped to the same box and scaled to `height` on-screen pixels."""

    def __init__(self, height: int, pixel_ratio: float):
        names = ["idle", "blink", "sleep"] + [f"walk_{i}" for i in range(WALK_FRAMES)]
        images = {n: QImage(str(SPRITE_DIR / f"{n}.png")) for n in names}
        box = QRect()
        for img in images.values():
            box = box.united(self._content_box(img))
        self.height = height
        self.width = round(box.width() * height / box.height())
        self.frames, self.mirrored = {}, {}
        for name, img in images.items():
            scaled = img.copy(box).scaled(round(self.width * pixel_ratio), round(height * pixel_ratio),
                                          Qt.KeepAspectRatio, Qt.SmoothTransformation)
            for store, image in ((self.frames, scaled), (self.mirrored, scaled.mirrored(True, False))):
                pix = QPixmap.fromImage(image)
                pix.setDevicePixelRatio(pixel_ratio)
                store[name] = pix

    @staticmethod
    def _content_box(img: QImage) -> QRect:
        """Smallest rectangle containing visible pixels (sampled every 2px for speed)."""
        top, bottom, left, right = img.height(), 0, img.width(), 0
        for y in range(0, img.height(), 2):
            for x in range(0, img.width(), 2):
                if img.pixelColor(x, y).alpha() > 10:
                    top, bottom = min(top, y), max(bottom, y)
                    left, right = min(left, x), max(right, x)
        return QRect(left, top, right - left + 2, bottom - top + 2)

    def frame(self, name: str, facing: int) -> QPixmap:
        return (self.frames if facing >= 0 else self.mirrored)[name]


def rebuild(look: dict) -> str:
    """Re-render the realistic tiny you for a new look. Returns '' on success, else an error."""
    if not Path(BLENDER).exists():
        return "Blender isn't installed, so I can't build my 3D look yet."
    from .outfits import body_for, outfit_for
    try:
        outfit = outfit_for(look)
    except ValueError as exc:
        return str(exc)
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    (AVATAR_DIR / "outfit.json").write_text(json.dumps(outfit, indent=2))
    (AVATAR_DIR / "body.json").write_text(json.dumps(body_for(look), indent=2))
    cmd = [BLENDER, "--background", "--python", str(ROOT / "avatar" / "build_tiny.py"), "--",
           "--look", str(life.DATA / "look.json"), "--body", str(AVATAR_DIR / "body.json"),
           "--outfit", str(AVATAR_DIR / "outfit.json"), "--out", str(AVATAR_DIR),
           "--size", "400", "--sprites"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "Blender took too long. Try again?"
    if "TINY_SPRITES" not in result.stdout:
        detail = (result.stderr or result.stdout).strip().splitlines()
        detail = "\n".join(detail[-8:])
        return "Blender couldn't build me this time. " + (detail or "Check the Blender log for details.")
    return ""
