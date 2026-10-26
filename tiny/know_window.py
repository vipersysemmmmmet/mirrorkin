"""What I know about you: everything tiny you has learned, and a way to delete it."""

from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListWidget,
                               QPushButton, QVBoxLayout, QWidget)

from . import learning, life


class KnowWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"What {life.APP_NAME} knows about you")
        self.resize(440, 420)
        intro = QLabel("Things I've picked up from our chats. Select any to forget them. "
                       "This stays on your computer.")
        intro.setWordWrap(True)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setWordWrap(True)
        forget, forget_all = QPushButton("Forget selected"), QPushButton("Forget everything")
        forget.clicked.connect(self.forget_selected)
        forget_all.clicked.connect(self.forget_all)
        row = QHBoxLayout()
        row.addWidget(forget)
        row.addStretch()
        row.addWidget(forget_all)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.list)
        layout.addLayout(row)

    def open(self) -> None:
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        self.list.clear()
        items = learning.facts()
        if not items:
            self.list.addItem("Nothing yet. Chat with me and I'll get to know you!")
            self.list.setEnabled(False)
            return
        self.list.setEnabled(True)
        for f in items:
            self.list.addItem(f"{f['fact']}  ({f['learned']})")

    def forget_selected(self) -> None:
        rows = [self.list.row(i) for i in self.list.selectedItems()]
        if rows and self.list.isEnabled():
            learning.forget(rows)
            self.refresh()

    def forget_all(self) -> None:
        learning.forget_all()
        self.refresh()
