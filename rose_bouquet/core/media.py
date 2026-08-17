"""The one small shape that outlived the local recommender.

`Candidate` used to live in `recommend.py`, alongside a ranker, an interest
filter and a store of everything you had watched — the feed built on this
machine. All of that is gone: the feeds come from the account now, so the
recommendations, the subscriptions and the history are YouTube's too.

What survived is only what still has a job: something that can be played or
downloaded, which is what every YouTube row hands around. It is here rather
than back in a module named after a recommender that no longer exists.
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
