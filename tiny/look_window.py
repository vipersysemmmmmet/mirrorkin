"""Change my look: a live-preview editor, plus "Fill from my photo"."""

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from .threads import keep
from . import look as looks
from .character import draw_character


class Preview(QWidget):
    def __init__(self, get_look):
        super().__init__()
        self.get_look = get_look
        self.frame = 0
        self.setFixedSize(220, 230)
        self.timer = QTimer(self, timeout=self.tick)
        self.timer.start(33)

    def tick(self) -> None:
        self.frame += 1
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(QRectF(0, 0, self.width(), self.height()), QColor("#efedf7"))
        p.scale(1.9, 1.9)
        blink = self.frame % 150 < 4
        draw_character(p, 58, 116, self.frame, False, False, blink, 1, self.get_look())


class PhotoJob(QThread):
    done = Signal(object, str)

    def __init__(self, jpeg: bytes):
        super().__init__()
        self.jpeg = jpeg

    def run(self) -> None:
        try:
            self.done.emit(looks.look_from_photo(self.jpeg), "")
        except looks.LookError as e:
            self.done.emit(None, str(e))


class Swatches(QWidget):
    """A row of colour buttons plus a custom-colour picker."""

    def __init__(self, colors: list[str], on_pick):
        super().__init__()
        self.on_pick = on_pick
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for c in colors:
            b = QPushButton()
            b.setFixedSize(22, 22)
            b.setToolTip(c)
            b.setStyleSheet(f"background:{c}; border:1px solid #999; border-radius:11px;")
            b.clicked.connect(lambda _=False, c=c: on_pick(c))
            row.addWidget(b)
        more = QPushButton("…")
        more.setFixedSize(26, 22)
        more.setToolTip("Pick any colour")
        more.clicked.connect(self.custom)
        row.addWidget(more)
        row.addStretch()

    def custom(self) -> None:
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            self.on_pick(color.name())


class LookWindow(QWidget):
    def __init__(self, on_save):
        super().__init__()
        self.on_save = on_save
        self.look = dict(looks.DEFAULT)
        self.job = None
        self.setWindowTitle("Change my look")

        self.preview = Preview(lambda: self.look)
        self.style = self.combo(looks.STYLES, "style")
        self.hair_style = self.combo(looks.HAIR_STYLES, "hair_style")
        self.facial_hair = self.combo(looks.FACIAL_HAIR, "facial_hair")
        self.extra = self.combo(looks.EXTRAS, "extra")
        self.top_style = self.combo(looks.TOP_STYLES, "top_style", editable=True)
        self.bottom_style = self.combo(looks.BOTTOM_STYLES, "bottom_style", editable=True)
        self.glasses = QCheckBox("Glasses")
        self.glasses.toggled.connect(lambda on: self.set("glasses", on))

        form = QFormLayout()
        form.addRow("Style", self.style)
        form.addRow("Skin", Swatches(looks.SKIN_TONES, lambda c: self.set("skin", c)))
        form.addRow("Hair", self.hair_style)
        form.addRow("Hair colour", Swatches(looks.HAIR_COLORS, lambda c: self.set("hair_color", c)))
        form.addRow("Facial hair", self.facial_hair)
        form.addRow("", self.glasses)
        form.addRow("Extra", self.extra)
        form.addRow("Top", self.top_style)
        form.addRow("Top colour", Swatches(["#5b4bdb", "#e04f5f", "#2f9e6b", "#f2a33a", "#2b2b2b", "#f5f5f5"],
                                    lambda c: self.set("top_color", c)))
        form.addRow("Bottoms", self.bottom_style)
        form.addRow("Bottoms colour", Swatches(["#1d1d1f", "#2c4a7c", "#6b5b4b", "#9a9a9a", "#e9dba6", "#f5f5f5"],
                                        lambda c: self.set("bottom_color", c)))

        self.photo = QPushButton("📷 Fill from my photo")
        self.photo.clicked.connect(self.from_photo)
        self.status = QLabel(wordWrap=True)
        self.status.setStyleSheet("color: gray")
        save, cancel = QPushButton("Save"), QPushButton("Cancel")
        save.setDefault(True)
        save.clicked.connect(self.save)
        cancel.clicked.connect(self.close)

        left = QVBoxLayout()
        left.addWidget(self.preview)
        left.addWidget(self.photo)
        left.addWidget(self.status)
        left.addStretch()
        top = QHBoxLayout()
        top.addLayout(left)
        top.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addLayout(buttons)

    def combo(self, options: list[str], key: str, editable: bool = False) -> QComboBox:
        box = QComboBox()
        box.addItems([o.capitalize() for o in options])
        box.setEditable(editable)
        if editable:
            box.setInsertPolicy(QComboBox.NoInsert)
            box.lineEdit().setPlaceholderText("Choose or type an installed asset name")
            box.currentTextChanged.connect(lambda value: self.set(key, value.strip().lower()))
        else:
            box.currentIndexChanged.connect(lambda i: self.set(key, options[i]))
        return box

    def set(self, key: str, value) -> None:
        self.look[key] = value

    def open(self, current: dict) -> None:
        self.apply(current)
        self.status.setText("Only these settings are saved. Photos are never stored. "
                            "The preview is the cartoon; Realistic is built in Blender when you save.")
        self.show()
        self.raise_()
        self.activateWindow()

    def apply(self, values: dict) -> None:
        self.look = {**self.look, **values}
        for box, key, options, editable in ((self.style, "style", looks.STYLES, False),
                                            (self.hair_style, "hair_style", looks.HAIR_STYLES, False),
                                            (self.facial_hair, "facial_hair", looks.FACIAL_HAIR, False),
                                            (self.extra, "extra", looks.EXTRAS, False),
                                            (self.top_style, "top_style", looks.TOP_STYLES, True),
                                            (self.bottom_style, "bottom_style", looks.BOTTOM_STYLES, True)):
            box.blockSignals(True)
            if editable:
                box.setCurrentText(self.look[key])
            else:
                box.setCurrentIndex(options.index(self.look[key]))
            box.blockSignals(False)
        self.glasses.blockSignals(True)
        self.glasses.setChecked(bool(self.look["glasses"]))
        self.glasses.blockSignals(False)

    def from_photo(self) -> None:
        if self.job:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose a photo of you", "",
                                              "Images (*.jpg *.jpeg *.png *.webp *.heic)")
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            self.status.setText("Couldn't open that image. Try a JPG or PNG.")
            return
        # Shrink before sending: smaller upload, and nothing beyond what's needed leaves the computer.
        image = image.scaled(768, 768, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        image.save(buf, "JPEG", 85)
        self.status.setText("Looking at your photo…")
        self.photo.setEnabled(False)
        self.job = keep(PhotoJob(bytes(buf.data())))
        self.job.done.connect(self.photo_done)
        self.job.start()

    def photo_done(self, result, error: str) -> None:
        self.job = None
        self.photo.setEnabled(True)
        if error:
            self.status.setText(error)
            return
        self.apply(result)
        self.status.setText("Done! Tweak anything that's off, then Save.")

    def save(self) -> None:
        self.on_save(dict(self.look))
        self.close()
