"""Running slow things off the interface thread.

Scanning a library, searching YouTube Music, downloading a track and importing a
playlist all take long enough to freeze a window, and all of them need to report
progress while they run. This is the one small piece of threading in the app, so
every one of them uses the same shape and no view has to know how it works.

`QThreadPool` rather than a thread each: a Spotify import that fires off a
hundred downloads should queue them, not open a hundred sockets at once and get
the machine rate-limited.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

logger = logging.getLogger(__name__)


class Signals(QObject):
    """A runnable cannot carry signals itself, so it borrows these."""

    finished = Signal(object)     # whatever the work returned
    failed = Signal(str)
    progress = Signal(object)     # anything the work chooses to report


class Job(QRunnable):
    """One piece of background work.

    The callable is given a `report` function to call with progress, if it wants
    one — checked by signature so simple jobs stay one-liners.
    """

    def __init__(self, work: Callable[..., Any], *args, wants_progress: bool = False, **kwargs) -> None:
        super().__init__()
        self.work = work
        self.args = args
        self.kwargs = kwargs
        self.wants_progress = wants_progress
        self.signals = Signals()
        self.cancelled = False

    def cancel(self) -> None:
        """Ask the job to stop. Cooperative — the work has to check `cancelled`."""
        self.cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            if self.wants_progress:
                result = self.work(*self.args, report=self.signals.progress.emit, **self.kwargs)
            else:
                result = self.work(*self.args, **self.kwargs)
        except Exception as exc:                  # noqa: BLE001 — the point is to not crash
            logger.exception("background job failed")
            self.signals.failed.emit(str(exc))
            return

        if not self.cancelled:
            self.signals.finished.emit(result)


def run(
    work: Callable[..., Any],
    *args,
    on_done: Optional[Callable[[Any], None]] = None,
    on_progress: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    pool: Optional[QThreadPool] = None,
    **kwargs,
) -> Job:
    """Run `work` in the background and call back on the interface thread."""
    job = Job(work, *args, wants_progress=on_progress is not None, **kwargs)

    if on_done is not None:
        job.signals.finished.connect(on_done)
    if on_progress is not None:
        job.signals.progress.connect(on_progress)
    if on_error is not None:
        job.signals.failed.connect(on_error)
    else:
        job.signals.failed.connect(lambda message: logger.warning("job failed: %s", message))

    (pool or QThreadPool.globalInstance()).start(job)
    return job


def downloads_pool() -> QThreadPool:
    """A separate, narrower pool for downloads.

    Three at a time: enough that a playlist import is not glacial, few enough
    that YouTube does not start refusing, and it leaves the global pool free for
    searches so the interface stays responsive mid-download.
    """
    pool = QThreadPool()
    pool.setMaxThreadCount(3)
    return pool
