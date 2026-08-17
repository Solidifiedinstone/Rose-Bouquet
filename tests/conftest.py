"""Settings that every test file needs before Qt is imported.

There is one job here: make sure a test run never puts a window on screen.

`os.environ.setdefault` is not enough and was the bug — a desktop session
exports `QT_QPA_PLATFORM` itself (`wayland;xcb` here), so `setdefault` finds a
value already there, keeps it, and the suite quietly opens real windows on the
real desktop. Forcing it is the only version that works, and it has to happen
here because conftest is imported before the test modules that import Qt.
"""

from __future__ import annotations

import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"
