"""Read look settings from a photo with no AI: a face detector plus pixel measurements.

Used whenever Claude isn't available (no credit, offline), so new people can always get
their 3D tiny. It measures colours (skin, hair, top, bottoms); the styles it can't see
reliably keep sensible defaults, and people fix them in their panel.
"""

import io

from PIL import Image


class NoFace(Exception):
    pass


def _find_face(img: Image.Image) -> tuple[int, int, int, int]:
    import cv2
    import numpy as np
    gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                      minSize=(max(24, img.width // 25), max(24, img.width // 25)))
    if len(faces) == 0:
        raise NoFace("I couldn't find a face. Try a photo of just you, facing the camera, in good light.")
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])  # the biggest face is the person
    return int(x), int(y), int(w), int(h)


def _colour(img: Image.Image, box, keep=(0.2, 0.8)) -> str | None:
    """Average colour of a region, after dropping its darkest and brightest pixels."""
    left, top, right, bottom = (int(v) for v in box)
    left, top = max(0, left), max(0, top)
    right, bottom = min(img.width, right), min(img.height, bottom)
    if right - left < 3 or bottom - top < 3:
        return None
    pixels = sorted(img.crop((left, top, right, bottom)).getdata(), key=sum)
    middle = pixels[int(len(pixels) * keep[0]): int(len(pixels) * keep[1])] or pixels
    r, g, b = (round(sum(p[c] for p in middle) / len(middle)) for c in range(3))
    return f"#{r:02x}{g:02x}{b:02x}"


def look_from_photo(jpeg: bytes) -> dict:
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    x, y, w, h = _find_face(img)
    look = {}
    # Cheeks and nose: below the eyes, inside the face
    look["skin"] = _colour(img, (x + w * 0.3, y + h * 0.5, x + w * 0.7, y + h * 0.75))
    # Hair: the band just above the forehead (darkest part, since background often shows through)
    hair = _colour(img, (x + w * 0.2, y - h * 0.25, x + w * 0.8, y + h * 0.02), keep=(0.0, 0.4))
    look["hair_color"] = hair
    # Bald or shaved: the band above the forehead looks like the skin
    if hair and look["skin"] and _distance(hair, look["skin"]) < 40:
        look["hair_style"] = "buzz"
    # Top: the chest, about one face below the chin; bottoms: around the hips, if the photo reaches
    look["top_color"] = _colour(img, (x, y + h * 1.6, x + w, y + h * 2.6))
    if y + h * 4.6 < img.height:
        look["bottom_color"] = _colour(img, (x + w * 0.1, y + h * 4.2, x + w * 0.9, y + h * 4.6))
    return {k: v for k, v in look.items() if v}


def _distance(a: str, b: str) -> float:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return sum((p - q) ** 2 for p, q in zip(ca, cb)) ** 0.5
