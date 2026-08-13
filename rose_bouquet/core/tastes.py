"""What you follow and what you like — kept entirely on this machine.

Rose Bouquet has no account, and that is a design decision rather than a
limitation. Everything a recommendation engine needs — what you subscribed to,
what you liked, what you actually finished listening to — is information you
generate. It does not have to be sent anywhere to be useful; it only has to be
sent somewhere if someone else wants to use it.

So subscriptions, likes and play history live in one JSON file next to the
library. It can be read, edited, backed up, copied to another machine, or
deleted. Deleting it deletes the algorithm's opinion of you, immediately and
completely, which is not a thing a hosted service can honestly offer.

Two consequences worth stating plainly:

  - **No cross-device sync unless you sync it yourself.** Put the folder in
    Syncthing and it follows you; do nothing and it stays here.
  - **No collaborative filtering.** "People who liked this also liked" needs
    other people's data. What is here instead is a model of *your* taste: the
    channels you follow, the artists you return to, and what you skip.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from rose_bouquet.core.interests import Interests

logger = logging.getLogger(__name__)

#: A play counts as real once this much has gone by. Below it, a skip.
FINISHED_FRACTION = 0.5

#: How long a signal keeps its full weight before it starts decaying. Taste
#: moves; a song on repeat two years ago should not outrank last week's.
HALF_LIFE_DAYS = 120


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "rose-bouquet"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _age_days(stamp: str, now: Optional[datetime] = None) -> float:
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, ((now or datetime.now()) - when).total_seconds() / 86400)


def decay(stamp: str, now: Optional[datetime] = None, half_life: float = HALF_LIFE_DAYS) -> float:
    """How much a signal from `stamp` still counts, 1.0 → 0.0.

    A half-life rather than a cliff: nothing is ever discarded outright, it just
    quietly matters less, which is how taste actually works.
    """
    return 0.5 ** (_age_days(stamp, now) / half_life)


@dataclass
class Channel:
    """Something you follow — an artist, a channel, a label."""

    id: str = ""
    title: str = ""
    thumbnail: str = ""
    #: artist | channel — artists come from YouTube Music, channels from YouTube.
    kind: str = "channel"
    subscribed_at: str = field(default_factory=_now)
    #: Set when you want a channel followed but muted in the feed.
    muted: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Channel":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class Signal:
    """One thing you did to one item, and when."""

    id: str = ""
    title: str = ""
    artist: str = ""
    channel_id: str = ""
    at: str = field(default_factory=_now)
    #: like | dislike | play | skip
    kind: str = "play"
    #: For plays: how much of it you listened to, 0.0–1.0.
    completion: float = 1.0
    #: video | short | music. Kept apart because they are different appetites:
    #: what somebody flicks through at one in the morning should not decide
    #: what their video feed shows them tomorrow, and the other way round.
    form: str = "video"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Signal":
        fields = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class Tastes:
    """Subscriptions, likes and history — the whole profile."""

    path: Optional[Path] = None
    channels: dict[str, Channel] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    #: Topics asked for by name, and topics never to show. Kept here rather
    #: than in preferences because it is part of the taste profile — the same
    #: file you can read, edit or delete to change what the feeds do.
    interests: Interests = field(default_factory=Interests)

    #: History is capped so the file cannot grow without limit. Twenty thousand
    #: signals is years of listening and still a small file.
    limit: int = 20000

    # ── Subscriptions ─────────────────────────────────────────────

    def subscribe(self, channel: Channel) -> Channel:
        existing = self.channels.get(channel.id)
        if existing is not None:
            return existing
        self.channels[channel.id] = channel
        return channel

    def unsubscribe(self, channel_id: str) -> None:
        self.channels.pop(channel_id, None)

    def subscribed(self, channel_id: str) -> bool:
        return channel_id in self.channels

    def subscriptions(self) -> list[Channel]:
        return sorted(self.channels.values(), key=lambda c: c.title.lower())

    def toggle_subscription(self, channel: Channel) -> bool:
        """Follow or unfollow. Returns whether you are now subscribed."""
        if self.subscribed(channel.id):
            self.unsubscribe(channel.id)
            return False
        self.subscribe(channel)
        return True

    # ── Likes and plays ───────────────────────────────────────────

    def record(self, signal: Signal) -> None:
        self.signals.append(signal)
        if len(self.signals) > self.limit:
            # Oldest first: the cap should cost you ancient history, not
            # yesterday.
            self.signals = self.signals[-self.limit:]

    def like(self, item_id: str, title: str = "", artist: str = "",
             channel_id: str = "", form: str = "video") -> bool:
        """Like something, or take the like back. Returns whether it is liked now."""
        if self.likes(item_id):
            self.signals = [
                s for s in self.signals
                if not (s.id == item_id and s.kind == "like")
            ]
            return False

        self.signals = [
            s for s in self.signals if not (s.id == item_id and s.kind == "dislike")
        ]
        self.record(Signal(id=item_id, title=title, artist=artist,
                           channel_id=channel_id, kind="like", form=form))
        return True

    def dislike(self, item_id: str, title: str = "", artist: str = "",
                channel_id: str = "") -> None:
        """Never show me this again — and less like it."""
        self.signals = [
            s for s in self.signals if not (s.id == item_id and s.kind == "like")
        ]
        self.record(Signal(id=item_id, title=title, artist=artist,
                           channel_id=channel_id, kind="dislike"))

    def likes(self, item_id: str) -> bool:
        return any(s.id == item_id and s.kind == "like" for s in self.signals)

    def dislikes(self, item_id: str) -> bool:
        return any(s.id == item_id and s.kind == "dislike" for s in self.signals)

    def of_form(self, *forms: str) -> list[Signal]:
        """Signals of a given kind of thing — videos, shorts, music.

        Anything recorded before this field existed reads as a video, which is
        what nearly all of it was.
        """
        wanted = set(forms)
        return [s for s in self.signals if (s.form or "video") in wanted]

    def liked(self) -> list[Signal]:
        """Everything liked, most recent first."""
        return sorted(
            (s for s in self.signals if s.kind == "like"),
            key=lambda s: s.at, reverse=True,
        )

    def note_play(self, item_id: str, title: str = "", artist: str = "",
                  channel_id: str = "", completion: float = 1.0,
                  form: str = "video") -> None:
        """Record a play — or a skip, which is just as informative.

        A skip is kept rather than ignored. "Played it and bailed after ten
        seconds" is one of the strongest signals there is, and a system that
        only records completions cannot tell enthusiasm from tolerance.
        """
        self.record(Signal(
            id=item_id, title=title, artist=artist, channel_id=channel_id,
            kind="play" if completion >= FINISHED_FRACTION else "skip",
            completion=max(0.0, min(1.0, completion)),
            form=form,
        ))

    def play_count(self, item_id: str) -> int:
        return sum(1 for s in self.signals if s.id == item_id and s.kind == "play")

    def recent(self, days: int = 30, kinds: tuple[str, ...] = ("play", "like")) -> list[Signal]:
        cutoff = datetime.now() - timedelta(days=days)
        recent = []
        for signal in self.signals:
            if signal.kind not in kinds:
                continue
            try:
                if datetime.fromisoformat(signal.at) >= cutoff:
                    recent.append(signal)
            except (TypeError, ValueError):
                continue
        return recent

    def forget(self, item_id: str) -> None:
        """Remove everything the profile knows about one item."""
        self.signals = [s for s in self.signals if s.id != item_id]

    def clear_history(self) -> None:
        """Drop plays and skips, keeping subscriptions and likes."""
        self.signals = [s for s in self.signals if s.kind in ("like", "dislike")]

    def clear_all(self) -> None:
        """Forget everything. The algorithm has no opinion of you afterwards."""
        self.signals = []
        self.channels = {}

    # ── Persistence ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "channels": [c.to_dict() for c in self.channels.values()],
            "signals": [s.to_dict() for s in self.signals],
            "interests": self.interests.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Tastes":
        tastes = cls()
        for row in data.get("channels", []) if isinstance(data.get("channels"), list) else []:
            if isinstance(row, dict):
                channel = Channel.from_dict(row)
                if channel.id:
                    tastes.channels[channel.id] = channel

        for row in data.get("signals", []) if isinstance(data.get("signals"), list) else []:
            if isinstance(row, dict):
                signal = Signal.from_dict(row)
                if signal.id:
                    tastes.signals.append(signal)

        tastes.interests = Interests.from_dict(data.get("interests") or {})
        return tastes

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Tastes":
        path = Path(path) if path else data_dir() / "tastes.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            tastes = cls()
        except (OSError, ValueError) as exc:
            logger.error("could not read %s: %s", path, exc)
            tastes = cls()
        else:
            tastes = cls.from_dict(data) if isinstance(data, dict) else cls()

        tastes.path = path
        return tastes

    def save(self, path: Optional[Path] = None) -> None:
        path = Path(path) if path else (self.path or data_dir() / "tastes.json")
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".part")
            temporary.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")
            temporary.replace(path)
        except OSError as exc:
            logger.error("could not save tastes to %s: %s", path, exc)
