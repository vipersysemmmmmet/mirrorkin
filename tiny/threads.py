"""Keep background workers alive until they've fully stopped.

Qt aborts the whole app if a QThread is destroyed while still running. Our "done" handlers
drop their reference as soon as the result arrives, which is a moment before the thread
actually finishes, so every worker is held here until its `finished` signal.
"""

_running = set()


def keep(thread):
    _running.add(thread)
    thread.finished.connect(lambda: _running.discard(thread))
    return thread
