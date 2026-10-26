"""Account & plan window: sign up, log in, link a wallet, and pay for a pass in USDC."""

from datetime import datetime

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout, QWidget)

from .threads import keep
from . import account, life


class Job(QThread):
    """Runs a server call off the UI thread."""
    done = Signal(object, str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self) -> None:
        try:
            self.done.emit(self.fn(*self.args), "")
        except account.ServerError as e:
            self.done.emit(None, str(e))


def clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


class AccountWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Account & plan")
        self.resize(460, 420)
        self.root = QVBoxLayout(self)
        self.job = None
        self.pricing = None
        self.status = QLabel()

    # ---- plumbing ----
    def run(self, on_done, fn, *args) -> None:
        if self.job:
            return
        self.say("Working…")
        self.job = keep(Job(fn, *args))

        def finished(result, error):
            self.job = None
            self.say(error, error=True) if error else self.say("")
            if not error:
                on_done(result)
        self.job.done.connect(finished)
        self.job.start()

    def say(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet("color: #c2412d" if error else "color: gray")

    def open(self) -> None:
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self) -> None:
        if account.logged_in():
            self.run(self.show_account, lambda: (account.call("GET", "/me"), account.call("GET", "/api/pricing")))
        else:
            self.show_login()

    # ---- logged out ----
    def show_login(self) -> None:
        clear(self.root)
        self.status = QLabel()
        title = QLabel(f"<h3>Log in to {life.APP_NAME}</h3>Your plan unlocks more chats and Scout runs.")
        title.setWordWrap(True)
        self.server = QLineEdit(account.server_url())
        self.email = QLineEdit(account.load().get("email", ""))
        self.password = QLineEdit(echoMode=QLineEdit.Password)
        form = QFormLayout()
        form.addRow("Server", self.server)
        form.addRow("Email", self.email)
        form.addRow("Password", self.password)
        log_in, sign_up = QPushButton("Log in"), QPushButton("Create account")
        log_in.clicked.connect(lambda: self.sign_in(False))
        sign_up.clicked.connect(lambda: self.sign_in(True))
        self.password.returnPressed.connect(lambda: self.sign_in(False))
        row = QHBoxLayout()
        row.addWidget(log_in)
        row.addWidget(sign_up)
        for w in (title,):
            self.root.addWidget(w)
        self.root.addLayout(form)
        self.root.addLayout(row)
        self.root.addWidget(self.status)
        self.root.addStretch()

    def sign_in(self, new: bool) -> None:
        def done(_me):
            life.log("signed up" if new else "logged in", self.email.text().strip())
            self.refresh()
        self.run(done, account.sign_in, self.server.text().strip().rstrip("/"),
                 self.email.text(), self.password.text(), new)

    # ---- logged in ----
    def show_account(self, result) -> None:
        me, self.pricing = result
        clear(self.root)
        self.status = QLabel()
        until = ""
        if me["expires_at"]:
            until = " until " + datetime.fromisoformat(me["expires_at"]).strftime("%b %d, %Y")
        elif me.get("trial_expires_at"):
            if me.get("trial_active"):
                until = " · trial ends " + datetime.fromisoformat(
                    me["trial_expires_at"]).strftime("%b %d, %Y")
            else:
                until = " · trial ended"
        lim, use = me["limits"], me["usage"]
        summary = QLabel(
            f"<b>{me['email']}</b><br>Plan: <b>{me['plan_name']}</b>{until}<br>"
            f"Chats today: {use['chats_today']} / {lim['chats_per_day']} · "
            f"Scout runs this week: {use['scouts_this_week']} / {lim['scouts_per_week']}")
        self.root.addWidget(summary)

        self.wallet = QLineEdit(me["wallet"] or "", placeholderText="0x… the wallet you'll pay from")
        save_wallet = QPushButton("Save")
        save_wallet.clicked.connect(self.save_wallet)
        row = QHBoxLayout()
        row.addWidget(self.wallet)
        row.addWidget(save_wallet)
        self.root.addWidget(QLabel("<br><b>Your wallet</b>"))
        self.root.addLayout(row)

        p = self.pricing
        self.root.addWidget(QLabel(f"<br><b>Buy a {p['pass_days']}-day pass with USDC</b>"))
        self.plan = QComboBox()
        for key in ("plus", "pro", "pro_max"):
            plan = p["plans"][key]
            self.plan.addItem(f"{plan['name']}: {plan['price']} USDC · {plan['chats_per_day']} chats/day · "
                              f"{plan['scouts_per_week']} Scout runs/week", key)
        self.plan.currentIndexChanged.connect(self.update_instructions)
        self.root.addWidget(self.plan)
        self.instructions = QLabel(wordWrap=True, textInteractionFlags=Qt.TextSelectableByMouse)
        self.root.addWidget(self.instructions)
        self.tx = QLineEdit(placeholderText="Paste the transaction hash (0x…)")
        verify = QPushButton("I've paid: verify")
        verify.clicked.connect(self.verify)
        row = QHBoxLayout()
        row.addWidget(self.tx)
        row.addWidget(verify)
        self.root.addLayout(row)
        self.update_instructions()

        out = QPushButton("Log out")
        out.clicked.connect(self.sign_out)
        self.root.addWidget(self.status)
        self.root.addStretch()
        self.root.addWidget(out)

    def update_instructions(self) -> None:
        p = self.pricing
        plan = p["plans"][self.plan.currentData()]
        if not p["pay_to"]:
            self.instructions.setText("Payments aren't set up on this server yet.")
            return
        self.instructions.setText(
            f"1. From your wallet, send <b>{plan['price']} USDC</b> on <b>{p['network_label']}</b> to:<br>"
            f"<code>{p['pay_to']}</code><br>"
            f"2. Paste the transaction hash below. Paying more buys whole extra passes. "
            f"Only send USDC on {p['network_label']}; other coins or networks can't be credited.")

    def save_wallet(self) -> None:
        self.run(lambda _: self.refresh(), account.call, "POST", "/wallet", {"wallet": self.wallet.text().strip()})

    def verify(self) -> None:
        def done(me):
            life.log("paid for a pass", f"{me['plan_name']} until {me['expires_at'][:10]}")
            self.refresh()
        self.run(done, account.call, "POST", "/pay",
                 {"plan": self.plan.currentData(), "tx_hash": self.tx.text().strip()})

    def sign_out(self) -> None:
        account.sign_out()
        life.log("logged out")
        self.show_login()
