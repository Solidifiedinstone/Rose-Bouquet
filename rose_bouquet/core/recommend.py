"""The algorithm — the whole thing, in one readable file.

This is the part a streaming service will not show you, so it is worth being
explicit: a candidate's score is the sum of a handful of named terms, every one
of which you can read, weigh differently, or turn off.

    affinity   how much you already listen to this artist or channel
    following  a flat bonus for things you subscribed to
    freshness  newer uploads over older ones, gently
    novelty    a penalty for what you have already heard a lot
    liked      a bonus for artists whose other work you liked
    dislike    a hard exclusion, not a nudge

Two properties are deliberate and worth defending:

**It cannot collapse into a rut.** Pure affinity ranking converges on the five
artists you play most and never leaves. The novelty term pushes against that,
and `explore` reserves part of every feed for things with no history at all.

**It explains itself.** Every scored item carries the terms that produced it, so
the interface can say *why* something is in your feed. A recommendation you
cannot interrogate is one you cannot correct.

All of it is pure functions over the local profile — no network, no accounts,
and testable without either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from rose_bouquet.core.tastes import Signal, Tastes, decay

#: The weights. Exposed as plain numbers because they are opinions, not physics.
WEIGHTS = {
    "affinity": 1.0,
    "following": 0.8,
    "freshness": 0.5,
    "novelty": 0.6,
    "liked": 1.2,
}

#: How much of a feed is held back for things you have no history with.
EXPLORE_SHARE = 0.25


@dataclass
class Candidate:
    """Something that could go in the feed."""

    id: str = ""
    title: str = ""
    artist: str = ""
    channel_id: str = ""
    kind: str = "song"          # song | video | album | playlist
    #: ISO date, when known. Used for freshness.
    published: str = ""
    duration: int = 0
    thumbnail: str = ""
    source: str = ""            # which shelf it came from, for the "why"


@dataclass
class Scored:
    """A candidate, its score, and why."""

    candidate: Candidate
    score: float = 0.0
    terms: dict[str, float] = field(default_factory=dict)

    @property
    def why(self) -> str:
        """The one-line explanation shown under a feed item."""
        if not self.terms:
            return ""

        name, value = max(self.terms.items(), key=lambda pair: abs(pair[1]))
        artist = self.candidate.artist or "this channel"

        return {
            "affinity": f"You listen to {artist}",
            "following": f"You follow {artist}",
            "liked": f"You liked something by {artist}",
            "freshness": "Recently uploaded",
            "novelty": "You have not heard this yet",
        }.get(name, "") if value > 0 else "Something different"


# ── Building a picture of your taste ──────────────────────────────

def affinity(tastes: Tastes, now: Optional[datetime] = None) -> dict[str, float]:
    """How much each artist and channel means to you, 0.0 upwards.

    Built from plays, likes and skips, each decayed by age. Normalised at the
    end so the numbers are comparable between a heavy listener and someone who
    opened the app last week.
    """
    scores: dict[str, float] = {}

    for signal in tastes.signals:
        weight = {
            "like": 3.0,
            "play": 1.0,
            "skip": -1.5,       # a skip is a real opinion, and a negative one
            "dislike": -4.0,
        }.get(signal.kind, 0.0)

        if not weight:
            continue

        aged = weight * decay(signal.at, now)
        for key in _keys(signal):
            scores[key] = scores.get(key, 0.0) + aged

    # Subscribing is itself a signal, and a deliberate one.
    for channel in tastes.channels.values():
        if channel.muted:
            continue
        for key in (channel.id, channel.title.lower()):
            if key:
                scores[key] = scores.get(key, 0.0) + 2.0 * decay(channel.subscribed_at, now)

    peak = max((abs(v) for v in scores.values()), default=0.0)
    if peak:
        scores = {k: v / peak for k, v in scores.items()}
    return scores


def _keys(signal: Signal) -> list[str]:
    """The identities a signal says something about: the channel and the artist."""
    keys = []
    if signal.channel_id:
        keys.append(signal.channel_id)
    if signal.artist:
        keys.append(signal.artist.lower())
    return keys


def liked_artists(tastes: Tastes) -> set[str]:
    return {s.artist.lower() for s in tastes.signals if s.kind == "like" and s.artist}


def heard(tastes: Tastes) -> dict[str, int]:
    """How many times each item has been played."""
    counts: dict[str, int] = {}
    for signal in tastes.signals:
        if signal.kind == "play":
            counts[signal.id] = counts.get(signal.id, 0) + 1
    return counts


# ── Scoring ───────────────────────────────────────────────────────

def freshness(published: str, now: Optional[datetime] = None) -> float:
    """1.0 for something uploaded today, trailing off over a couple of months."""
    if not published:
        return 0.0
    return decay(published, now, half_life=45)


def score(
    candidate: Candidate,
    tastes: Tastes,
    *,
    affinities: Optional[dict[str, float]] = None,
    plays: Optional[dict[str, int]] = None,
    likes: Optional[set[str]] = None,
    weights: Optional[dict[str, float]] = None,
    now: Optional[datetime] = None,
) -> Scored:
    """Score one candidate, keeping the terms that produced the number."""
    affinities = affinities if affinities is not None else affinity(tastes, now)
    plays = plays if plays is not None else heard(tastes)
    likes = likes if likes is not None else liked_artists(tastes)
    weights = {**WEIGHTS, **(weights or {})}

    terms: dict[str, float] = {}
    artist_key = candidate.artist.lower()

    strength = max(
        affinities.get(candidate.channel_id, 0.0),
        affinities.get(artist_key, 0.0),
    )
    if strength:
        terms["affinity"] = weights["affinity"] * strength

    if tastes.subscribed(candidate.channel_id):
        channel = tastes.channels.get(candidate.channel_id)
        if channel is not None and not channel.muted:
            terms["following"] = weights["following"]

    fresh = freshness(candidate.published, now)
    if fresh:
        terms["freshness"] = weights["freshness"] * fresh

    if artist_key and artist_key in likes:
        terms["liked"] = weights["liked"]

    # Novelty: unheard things get the full bonus, and it falls away as the play
    # count climbs. Without this the feed becomes the same ten songs.
    count = plays.get(candidate.id, 0)
    terms["novelty"] = weights["novelty"] * (1.0 / (1.0 + count))

    return Scored(candidate=candidate, score=sum(terms.values()), terms=terms)


def rank(
    candidates: Iterable[Candidate],
    tastes: Tastes,
    *,
    limit: int = 50,
    explore: float = EXPLORE_SHARE,
    weights: Optional[dict[str, float]] = None,
    now: Optional[datetime] = None,
) -> list[Scored]:
    """Order candidates by score, with room reserved for the unfamiliar.

    Dislikes are dropped outright rather than scored low: "not this" should mean
    not this, not "this, further down".

    The explore share is taken from candidates with no affinity at all, so a
    feed always contains something you have no history with. A recommender that
    only ever confirms what it already knows is a recommender that slowly stops
    being useful.
    """
    affinities = affinity(tastes, now)
    plays = heard(tastes)
    likes = liked_artists(tastes)
    # Gathered once. `tastes.dislikes()` walks every signal, so asking it per
    # candidate turned ranking a thousand candidates against a few thousand
    # signals into millions of comparisons.
    disliked = {s.id for s in tastes.signals if s.kind == "dislike"}

    seen: set[str] = set()
    scored: list[Scored] = []

    for candidate in candidates:
        if not candidate.id or candidate.id in seen:
            continue
        if candidate.id in disliked:
            continue
        seen.add(candidate.id)
        scored.append(score(
            candidate, tastes, affinities=affinities, plays=plays,
            likes=likes, weights=weights, now=now,
        ))

    scored.sort(key=lambda s: s.score, reverse=True)
    if len(scored) <= limit:
        return scored

    familiar = []
    unfamiliar = []
    for item in scored:
        known = item.terms.get("affinity") or item.terms.get("following")
        (familiar if known else unfamiliar).append(item)

    room = max(0, int(limit * explore))
    chosen = familiar[:limit - room] + unfamiliar[:room]

    # Membership by id rather than by object: `Scored` is a dataclass, so `in`
    # compares its candidate and its terms dict field by field, which turns
    # topping up the list into a quadratic crawl.
    taken = {item.candidate.id for item in chosen}
    if len(chosen) < limit:
        for item in scored:
            if item.candidate.id not in taken:
                chosen.append(item)
                taken.add(item.candidate.id)
            if len(chosen) >= limit:
                break

    chosen.sort(key=lambda s: s.score, reverse=True)
    return chosen[:limit]


# ── Shelves ───────────────────────────────────────────────────────

def top_artists(tastes: Tastes, limit: int = 10, now: Optional[datetime] = None) -> list[tuple[str, float]]:
    """Your most-listened artists, strongest first — for "more like this"."""
    scores = affinity(tastes, now)
    named = [
        (key, value) for key, value in scores.items()
        if value > 0 and not key.startswith("UC")     # channel ids, not names
    ]
    named.sort(key=lambda pair: pair[1], reverse=True)
    return named[:limit]


def seeds(tastes: Tastes, limit: int = 8) -> list[Signal]:
    """The items worth asking YouTube for related tracks to.

    Recent likes first, then things played repeatedly. These are what the feed
    is grown from, and keeping the list short keeps the feed fast.
    """
    plays = heard(tastes)
    liked = tastes.liked()

    repeated = sorted(
        (s for s in tastes.signals if s.kind == "play" and plays.get(s.id, 0) >= 3),
        key=lambda s: plays.get(s.id, 0), reverse=True,
    )

    chosen: list[Signal] = []
    seen: set[str] = set()
    for signal in [*liked, *repeated]:
        if signal.id in seen:
            continue
        seen.add(signal.id)
        chosen.append(signal)
        if len(chosen) >= limit:
            break

    return chosen
