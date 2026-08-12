"""The queue: what is playing, what is next, and what shuffle actually means.

No audio and no Qt in here. The queue is a list, a cursor and two modes, and
every question the interface asks — what is next, what happens when this ends,
what does the up-next list look like — is answered from those. Keeping it apart
from the audio backend is what makes shuffle testable without a sound card.

**Shuffle is a shuffled order, not a random pick.** Picking at random each time
replays tracks before the album is through and has no meaningful "previous".
Instead the queue keeps a permutation: turning shuffle on reorders what is
coming, turning it off puts the original order back, and the track playing at
the time keeps playing either way.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional

from rose_bouquet.core.library import Track


class Repeat(str, Enum):
    OFF = "off"
    ALL = "all"
    ONE = "one"

    def next(self) -> "Repeat":
        order = [Repeat.OFF, Repeat.ALL, Repeat.ONE]
        return order[(order.index(self) + 1) % len(order)]

    @property
    def label(self) -> str:
        return {Repeat.OFF: "Repeat off", Repeat.ALL: "Repeat all",
                Repeat.ONE: "Repeat one"}[self]


@dataclass
class Queue:
    """An ordered list of tracks and a position in it."""

    #: The queue in the order it was built — album order, playlist order.
    tracks: list[Track] = field(default_factory=list)
    #: Index into `order`, not into `tracks`.
    position: int = 0
    #: Indices into `tracks`, in the order they will actually play.
    order: list[int] = field(default_factory=list)

    shuffle: bool = False
    repeat: Repeat = Repeat.OFF

    #: Seeded for tests; None uses the global random state.
    rng: Optional[random.Random] = None

    # ── What is playing ───────────────────────────────────────────

    @property
    def current(self) -> Optional[Track]:
        if not self.order or not 0 <= self.position < len(self.order):
            return None
        index = self.order[self.position]
        return self.tracks[index] if 0 <= index < len(self.tracks) else None

    @property
    def upcoming(self) -> list[Track]:
        """What is queued after the current track, in the order it will play."""
        return [self.tracks[i] for i in self.order[self.position + 1:]]

    @property
    def history(self) -> list[Track]:
        return [self.tracks[i] for i in self.order[:self.position]]

    def __len__(self) -> int:
        return len(self.tracks)

    # ── Filling it ────────────────────────────────────────────────

    def set_tracks(self, tracks: Iterable[Track], start: int = 0) -> Optional[Track]:
        """Replace the queue and start at one of them.

        With shuffle on, the chosen track still plays first — picking an album
        track and being given a different one is not what anybody meant.
        """
        self.tracks = list(tracks)
        self.order = list(range(len(self.tracks)))

        if self.shuffle:
            self._shuffle_around(start)
        else:
            self.position = max(0, min(start, len(self.order) - 1)) if self.order else 0

        return self.current

    def enqueue(self, tracks: Iterable[Track]) -> None:
        """Add to the end of the queue, keeping the current track playing."""
        for track in tracks:
            self.tracks.append(track)
            self.order.append(len(self.tracks) - 1)

    def play_next(self, tracks: Iterable[Track]) -> None:
        """Slot tracks in immediately after whatever is playing."""
        for offset, track in enumerate(tracks):
            self.tracks.append(track)
            self.order.insert(self.position + 1 + offset, len(self.tracks) - 1)

    def remove_at(self, upcoming_index: int) -> None:
        """Drop one entry from the up-next list."""
        target = self.position + 1 + upcoming_index
        if 0 <= target < len(self.order):
            del self.order[target]

    def clear(self) -> None:
        self.tracks = []
        self.order = []
        self.position = 0

    # ── Moving through it ─────────────────────────────────────────

    def next(self, *, manual: bool = False) -> Optional[Track]:
        """The next track, honouring repeat. None means the queue is finished.

        `manual` is a skip the user asked for. It matters for repeat-one: a
        track set to repeat should repeat when it *ends*, but pressing skip
        should still move on. A repeat mode that ignores the skip button is a
        bug people report as "skip is broken".
        """
        if not self.order:
            return None

        if self.repeat is Repeat.ONE and not manual:
            return self.current

        if self.position + 1 < len(self.order):
            self.position += 1
            return self.current

        if self.repeat is Repeat.ALL:
            # Round the end. With shuffle on, reshuffle so the second pass is
            # not the first pass again.
            if self.shuffle:
                self._reshuffle_all()
            self.position = 0
            return self.current

        return None

    def previous(self, *, restart_after: float = 3.0, elapsed: float = 0.0) -> Optional[Track]:
        """Previous track — or the start of this one if it is already under way.

        The rule every music player has converged on: pressing back a few
        seconds in means "start this again", and pressing it immediately means
        "the one before".
        """
        if elapsed >= restart_after:
            return self.current

        if self.position > 0:
            self.position -= 1
        elif self.repeat is Repeat.ALL and self.order:
            self.position = len(self.order) - 1

        return self.current

    def jump_to(self, index: int) -> Optional[Track]:
        """Play the nth track of the queue as it currently stands."""
        if 0 <= index < len(self.order):
            self.position = index
        return self.current

    def jump_to_track(self, track: Track) -> Optional[Track]:
        for position, index in enumerate(self.order):
            if self.tracks[index] is track or self.tracks[index].path == track.path:
                self.position = position
                return self.current
        return self.current

    # ── Shuffle ───────────────────────────────────────────────────

    def set_shuffle(self, shuffle: bool) -> None:
        """Turn shuffle on or off without interrupting what is playing."""
        if shuffle == self.shuffle:
            return

        playing = self.order[self.position] if self.order else None
        self.shuffle = shuffle

        if not self.order:
            return

        if shuffle:
            self._shuffle_around(playing or 0)
        else:
            # Back to natural order, with the cursor following the same track.
            self.order = list(range(len(self.tracks)))
            self.position = playing if playing is not None else 0

    def toggle_shuffle(self) -> bool:
        self.set_shuffle(not self.shuffle)
        return self.shuffle

    def cycle_repeat(self) -> Repeat:
        self.repeat = self.repeat.next()
        return self.repeat

    def _shuffle_around(self, index: int) -> None:
        """Shuffle everything, then put `index` first and point at it."""
        rest = [i for i in range(len(self.tracks)) if i != index]
        (self.rng or random).shuffle(rest)
        self.order = ([index] + rest) if self.tracks else []
        self.position = 0

    def _reshuffle_all(self) -> None:
        order = list(range(len(self.tracks)))
        (self.rng or random).shuffle(order)
        self.order = order

    # ── Serialisation ─────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Enough to restore the queue on the next run.

        Tracks are stored as paths and looked back up in the library, so a queue
        saved yesterday does not resurrect stale copies of tags edited since.
        """
        return {
            "paths": [t.path for t in self.tracks],
            "order": list(self.order),
            "position": self.position,
            "shuffle": self.shuffle,
            "repeat": self.repeat.value,
        }

    @classmethod
    def from_dict(cls, data: dict, resolve) -> "Queue":
        queue = cls()
        if not isinstance(data, dict):
            return queue

        paths = data.get("paths")
        if isinstance(paths, list):
            queue.tracks = [t for t in (resolve(str(p)) for p in paths) if t is not None]

        order = data.get("order")
        # An order referring to tracks that have since vanished is rebuilt
        # rather than trusted; a stale index would play the wrong song.
        if isinstance(order, list) and len(order) == len(queue.tracks) and all(
            isinstance(i, int) and 0 <= i < len(queue.tracks) for i in order
        ):
            queue.order = order
        else:
            queue.order = list(range(len(queue.tracks)))

        try:
            queue.position = max(0, min(int(data.get("position", 0)), len(queue.order) - 1))
        except (TypeError, ValueError):
            queue.position = 0

        queue.shuffle = bool(data.get("shuffle"))
        try:
            queue.repeat = Repeat(data.get("repeat", "off"))
        except ValueError:
            queue.repeat = Repeat.OFF

        return queue
