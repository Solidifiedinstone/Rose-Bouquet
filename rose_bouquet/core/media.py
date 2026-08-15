"""The small shapes that outlived the local recommender.

These two dataclasses used to live in `recommend.py` and `tastes.py`, which
were the feed built on this machine — a ranker, an interest filter and a store
of everything you had watched. All of that is gone: the YouTube tab is
YouTube's own site now, so the recommendations, the subscriptions and the
history are YouTube's too.

What survived is only what still has a job. `Candidate` is a thing that can be
streamed or downloaded, which the YouTube Music tab still hands around, and
`Channel` is who uploaded it. They are here rather than back in a module named
after a recommender that no longer exists.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Candidate:
    """Something that can be played or downloaded."""

    id: str = ""
    title: str = ""
    artist: str = ""
    channel_id: str = ""
    kind: str = "song"          # song | video | album | playlist
    #: ISO date, when known.
    published: str = ""
    duration: int = 0
    thumbnail: str = ""
    #: Where it was found — kept for the downloads list, not for scoring.
    source: str = ""


@dataclass
class Channel:
    """Whoever published something — an artist, a channel, a label."""

    id: str = ""
    title: str = ""
    thumbnail: str = ""
    #: artist | channel — artists come from YouTube Music, channels from YouTube.
    kind: str = "channel"
