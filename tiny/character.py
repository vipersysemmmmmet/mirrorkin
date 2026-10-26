"""Draws tiny you from its look settings (see look.py)."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

INK = QColor("#1d1d1f")


def _hair_cap(cx: float, head_y: float, height: float) -> QPainterPath:
    """Hair covering the top of the head; smaller `height` = shorter hair."""
    path = QPainterPath()
    path.moveTo(cx - 23, head_y + 20)
    path.cubicTo(cx - 24, head_y - height, cx + 24, head_y - height, cx + 23, head_y + 20)
    path.cubicTo(cx + 10, head_y + 6, cx - 10, head_y + 6, cx - 23, head_y + 20)
    return path


def _hair_back(p: QPainter, style: str, cx: float, head_y: float) -> None:
    """Hair that sits behind the head."""
    if style == "long":
        p.drawRoundedRect(QRectF(cx - 26, head_y + 4, 52, 58), 18, 18)
    elif style == "long curly":
        # A cloud of curls framing the face down to the shoulders
        p.drawEllipse(QRectF(cx - 31, head_y - 10, 62, 56))
        for y in range(10, 62, 9):
            for side in (-1, 1):
                p.drawEllipse(QPointF(cx + side * (25 - y / 12), head_y + y), 8.5, 8.5)
    elif style == "afro":
        p.drawEllipse(QRectF(cx - 33, head_y - 16, 66, 58))
    elif style == "braids":
        p.drawRoundedRect(QRectF(cx - 28, head_y + 14, 8, 50), 4, 4)
        p.drawRoundedRect(QRectF(cx + 20, head_y + 14, 8, 50), 4, 4)
    elif style == "bun":
        p.drawEllipse(QRectF(cx - 11, head_y - 14, 22, 20))


def _hair_front(p: QPainter, style: str, cx: float, head_y: float) -> None:
    """Hair over the forehead and top of the head."""
    if style == "bald":
        return
    if style == "buzz":
        p.drawPath(_hair_cap(cx, head_y, -2))
    elif style in ("curly", "long curly"):
        for deg in range(-170, 0, 22):
            a = math.radians(deg)
            p.drawEllipse(QPointF(cx + 20 * math.cos(a), head_y + 21 + 20 * math.sin(a)), 7.5, 7.5)
    else:
        p.drawPath(_hair_cap(cx, head_y, 6))


def draw_character(p: QPainter, cx: float, base: float, frame: int, walking: bool,
                   sleeping: bool, blinking: bool, facing: int, look: dict) -> None:
    """Draw tiny you standing on y=base, centred on x=cx."""
    skin, hair = QColor(look["skin"]), QColor(look["hair_color"])
    top_c, bottom_c = QColor(look["top_color"]), QColor(look["bottom_color"])
    bob = math.sin(frame / 4) * 2 if walking else math.sin(frame / 15) * 1
    p.setPen(Qt.NoPen)

    # Shadow
    p.setBrush(QColor(0, 0, 0, 40))
    p.drawEllipse(QRectF(cx - 24, base - 6, 48, 10))

    # Legs and shoes (bare legs under a skirt)
    skirt = "skirt" in look.get("bottom_style", "")
    swing = math.sin(frame / 3) * 5 if walking else 0
    for x in (cx - 12 + swing, cx + 3 - swing):
        p.setBrush(skin if skirt else bottom_c)
        p.drawRoundedRect(QRectF(x + (1 if skirt else 0), base - 22, 7 if skirt else 9, 18), 4, 4)
        p.setBrush(INK)
        p.drawRoundedRect(QRectF(x - 1, base - 7, 11, 6), 3, 3)

    top = base - 58 + bob
    head_y = top - 40

    if skirt:
        flare = QPainterPath()
        flare.moveTo(cx - 20, top + 26)
        flare.lineTo(cx + 20, top + 26)
        flare.lineTo(cx + 26, base - 11)
        flare.quadTo(cx, base - 7, cx - 26, base - 11)
        flare.closeSubpath()
        p.setBrush(bottom_c)
        p.drawPath(flare)

    # Hair behind the head goes first, so the body and face sit in front of it
    p.setBrush(hair)
    _hair_back(p, look["hair_style"], cx, head_y)

    # Body
    p.setBrush(top_c)
    p.drawRoundedRect(QRectF(cx - 22, top, 44, 32 if skirt else 40), 14, 14)

    # "AI" badge: tiny you is always labeled as AI, so it must stay readable on any top colour
    light_top = top_c.lightness() > 200
    p.setPen(QPen(INK, 1) if light_top else Qt.NoPen)
    p.setBrush(QColor("white"))
    p.drawEllipse(QRectF(cx - 9, top + 12, 18, 18))
    p.setPen(INK if light_top else top_c)
    p.setFont(QFont("Helvetica", 7, QFont.Bold))
    p.drawText(QRectF(cx - 9, top + 12, 18, 18), Qt.AlignCenter, "AI")
    p.setPen(Qt.NoPen)

    # Head
    p.setBrush(skin)
    p.drawEllipse(QRectF(cx - 22, head_y, 44, 44))
    turn = 3 * facing if walking else 0

    # Beard sits under the eyes and mouth
    if look["facial_hair"] == "beard":
        p.setBrush(hair)
        p.drawChord(QRectF(cx - 22, head_y + 6, 44, 40), 180 * 16, 180 * 16)
        p.setBrush(skin)
        p.drawEllipse(QRectF(cx - 7 + turn, head_y + 28, 14, 9))

    # Hair over the head
    p.setBrush(hair)
    _hair_front(p, look["hair_style"], cx, head_y)

    # Eyes
    eye_y = head_y + 24
    if sleeping or blinking:
        p.setPen(QPen(INK, 1.5))
        p.drawLine(QPointF(cx - 10 + turn, eye_y), QPointF(cx - 4 + turn, eye_y))
        p.drawLine(QPointF(cx + 4 + turn, eye_y), QPointF(cx + 10 + turn, eye_y))
        p.setPen(Qt.NoPen)
    else:
        p.setBrush(INK)
        p.drawEllipse(QRectF(cx - 10 + turn, eye_y - 3, 6, 7))
        p.drawEllipse(QRectF(cx + 4 + turn, eye_y - 3, 6, 7))
        # Sparkles keep the eyes readable on every skin tone
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(cx - 8.5 + turn, eye_y - 2, 2.2, 2.2))
        p.drawEllipse(QRectF(cx + 5.5 + turn, eye_y - 2, 2.2, 2.2))

    # Cheeks
    p.setBrush(QColor(235, 120, 120, 90))
    p.drawEllipse(QRectF(cx - 18, head_y + 28, 7, 5))
    p.drawEllipse(QRectF(cx + 11, head_y + 28, 7, 5))

    # Mustache, then mouth
    if look["facial_hair"] == "mustache":
        p.setBrush(hair)
        p.drawRoundedRect(QRectF(cx - 7 + turn, head_y + 27, 14, 4), 2, 2)
    # Light mouth line on deep skin tones so it stays visible
    p.setPen(QPen(QColor("#f3d6c8") if skin.lightness() < 90 else INK, 1.4))
    p.setBrush(Qt.NoBrush)
    if sleeping:
        p.drawEllipse(QRectF(cx - 2 + turn, head_y + 32, 4, 4))
    else:
        p.drawArc(QRectF(cx - 6 + turn, head_y + 29, 12, 8), 200 * 16, 140 * 16)

    # Glasses
    if look["glasses"]:
        p.setPen(QPen(QColor(look.get("glasses_color", "#1d1d1f")), 1.6))
        p.drawEllipse(QRectF(cx - 13 + turn, eye_y - 6, 11, 11))
        p.drawEllipse(QRectF(cx + 2 + turn, eye_y - 6, 11, 11))
        p.drawLine(QPointF(cx - 2 + turn, eye_y - 1), QPointF(cx + 2 + turn, eye_y - 1))
    p.setPen(Qt.NoPen)

    # Extras
    extra = look["extra"]
    if extra == "earrings":
        p.setBrush(QColor("#e2b13c"))
        p.drawEllipse(QRectF(cx - 25, head_y + 28, 5, 5))
        p.drawEllipse(QRectF(cx + 20, head_y + 28, 5, 5))
    elif extra == "cap":
        p.setBrush(top_c)
        p.drawChord(QRectF(cx - 23, head_y - 3, 46, 34), 0, 180 * 16)
        brim = cx - 2 if facing >= 0 else cx - 28
        p.drawRoundedRect(QRectF(brim, head_y + 11, 30, 5), 2, 2)
    elif extra == "headphones":
        p.setPen(QPen(QColor("#444"), 4))
        p.drawArc(QRectF(cx - 25, head_y - 5, 50, 46), 15 * 16, 150 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#444"))
        p.drawRoundedRect(QRectF(cx - 29, head_y + 15, 9, 15), 4, 4)
        p.drawRoundedRect(QRectF(cx + 20, head_y + 15, 9, 15), 4, 4)

    # Snoring
    if sleeping:
        p.setPen(INK)
        p.setFont(QFont("Helvetica", 11, QFont.Bold))
        z = (frame // 10) % 3
        p.drawText(QPointF(cx + 22, head_y + 4 - z * 6), "z" * (z + 1))
        p.setPen(Qt.NoPen)
