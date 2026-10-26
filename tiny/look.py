"""What tiny you looks like: simple settings the character is drawn from.

Only these settings are saved. A photo used to fill them in is never stored.
"""

import base64
import json
import os

from . import life

LOOK_FILE = life.DATA / "look.json"

HAIR_STYLES = ["short", "long", "curly", "long curly", "afro", "bun", "updo", "ponytail", "bob",
               "braids", "cornrows", "buzz", "bald"]
FACIAL_HAIR = ["none", "mustache", "beard"]
EXTRAS = ["none", "earrings", "cap", "headphones"]
TOP_STYLES = ["t-shirt", "long sleeves", "sweater", "tank top", "camisole", "turtleneck", "corset top",
              "tube top", "shirt", "jacket", "suit", "polo"]
BOTTOM_STYLES = ["pants", "jeans", "shorts", "skirt", "mini skirt", "long skirt"]
SKIN_TONES = ["#fbe0cf", "#f2c9a0", "#e0ac7e", "#c68a5e", "#a8693f", "#8d5524", "#6b3e1f", "#4a2a17"]
HAIR_COLORS = ["#1c1714", "#3b2a1e", "#6a4a32", "#8e4a24", "#c79a5b", "#e7cf8f", "#9a9a9a", "#e8e8e8"]

STYLES = ["realistic", "cartoon"]

DEFAULT = {
    "style": "realistic",
    "skin": "#f2c9a0", "hair_style": "short", "hair_color": "#2b2118",
    "facial_hair": "none", "glasses": False, "extra": "none",
    "top_style": "t-shirt", "top_color": "#5b4bdb", "bottom_style": "pants", "bottom_color": "#1d1d1f",
}


def load() -> dict:
    if LOOK_FILE.exists():
        return {**DEFAULT, **json.loads(LOOK_FILE.read_text())}
    return dict(DEFAULT)


def save(look: dict) -> None:
    life.DATA.mkdir(exist_ok=True)
    LOOK_FILE.write_text(json.dumps(look, indent=2))


HEX = {"type": "string", "description": "Colour as #rrggbb"}
PHOTO_SCHEMA = {
    "type": "object",
    "properties": {
        "found_person": {"type": "boolean", "description": "True if exactly one person's face is clearly visible"},
        "face_box": {"type": "array", "items": {"type": "number"},
                     "description": "The face (forehead to chin, ear to ear) as [left, top, right, bottom], "
                                    "each a fraction 0-1 of the image width or height"},
        "skin": HEX,
        "hair_style": {"type": "string", "enum": HAIR_STYLES},
        "hair_color": HEX,
        "facial_hair": {"type": "string", "enum": FACIAL_HAIR},
        "glasses": {"type": "boolean"},
        "extra": {"type": "string", "enum": EXTRAS},
        "top_style": {"type": "string", "enum": TOP_STYLES},
        "top_color": HEX,
        "bottom_style": {"type": "string", "enum": BOTTOM_STYLES},
        "bottom_color": HEX,
    },
    "required": ["found_person", "face_box", "skin", "hair_style", "hair_color", "facial_hair",
                 "glasses", "extra", "top_style", "top_color", "bottom_style", "bottom_color"],
    "additionalProperties": False,
}


class LookError(Exception):
    pass


def look_from_photo(jpeg: bytes) -> dict:
    """Ask Claude to turn a photo into avatar settings. Returns only the settings."""
    import anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return _look_with_claude_code(jpeg)
    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(jpeg).decode()}},
                {"type": "text", "text": PHOTO_PROMPT},
            ]}],
            thinking={"type": "adaptive"},
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": PHOTO_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIError as e:
        raise LookError(f"Couldn't read the photo: {e}")
    if response.stop_reason == "refusal":
        raise LookError("That photo couldn't be used. Try a different one.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return _finish(json.loads(text), jpeg)


PHOTO_PROMPT = ("This photo is of the user, who wants a mini avatar that looks like them. Pick the closest "
                "settings for what is visible: skin tone (sample the face in good light, not shadow), "
                "hairstyle, hair colour, facial hair, glasses, any earrings, cap or headphones, the kind of "
                "top (sleeve length matters) and its main colour, and the kind of bottoms and their colour. "
                "Describe appearance only.")


def _look_with_claude_code(jpeg: bytes) -> dict:
    from . import claude_code
    try:
        result = claude_code.ask(PHOTO_PROMPT, "You turn photos into avatar settings.",
                                 image=jpeg, schema=PHOTO_SCHEMA)
    except claude_code.ClaudeCodeError as e:
        raise LookError(str(e))
    return _finish(result, jpeg)


def _finish(result: dict, jpeg: bytes) -> dict:
    if not result.pop("found_person"):
        raise LookError("I couldn't see one clear face. Try a photo of just you, facing the camera.")
    measured = skin_from_pixels(jpeg, result.pop("face_box", None))
    if measured:
        result["skin"] = measured
    return result


def skin_from_pixels(jpeg: bytes, box) -> str | None:
    """The skin colour measured from the photo itself, which is far truer than a described colour.

    Samples the cheeks and nose area (the middle of the face box, below the eyes), drops the darkest
    and brightest pixels (shadow, hair, glare) and averages the rest."""
    if not isinstance(box, list) or len(box) != 4:
        return None
    import io
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    except Exception:
        return None
    left, top, right, bottom = (min(1.0, max(0.0, float(v))) for v in box)
    if right - left < 0.01 or bottom - top < 0.01:
        return None
    w, h = img.size
    fw, fh = (right - left) * w, (bottom - top) * h
    area = img.crop((int(left * w + fw * 0.25), int(top * h + fh * 0.45),
                     int(left * w + fw * 0.75), int(top * h + fh * 0.75)))
    pixels = sorted(area.getdata(), key=lambda p: sum(p))
    if len(pixels) < 20:
        return None
    middle = pixels[len(pixels) // 5: len(pixels) * 4 // 5]
    r, g, b = (round(sum(p[c] for p in middle) / len(middle)) for c in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"
