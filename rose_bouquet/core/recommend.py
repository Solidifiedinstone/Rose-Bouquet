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

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from rose_bouquet.core.tastes import Signal, Tastes, decay

logger = logging.getLogger(__name__)

#: The weights. Exposed as plain numbers because they are opinions, not physics.
WEIGHTS = {
    "affinity": 1.0,
    "following": 0.8,
    "freshness": 0.5,
    "novelty": 0.6,
    "liked": 1.2,
    #: A topic asked for by name. The largest single weight on purpose: what
    #: somebody typed in beats anything this file inferred about them.
    "wanted": 2.0,
    #: Something found by looking, rather than something from a channel
    #: already followed. Without this a discovery scores on novelty alone,
    #: which every subscription beats — so it lands below sixty familiar rows
    #: where nobody scrolls, and the feed can only ever confirm what you
    #: already watch.
    "discovered": 0.9,
}

#: How much of the shorter title's words two things must share to count as
#: the same thing. Two thirds: enough that a repost with an extra word in it
#: collapses, not so much that two videos about one subject do.
SAME_TITLE = 0.66
SAME_TITLE_WORDS = 3

#: Sources that mean "this was found by looking", as opposed to arriving from
#: a channel already followed.
DISCOVERED = ("discover", "similar", "shorts", "related")

#: How much of a feed is held back for things you have no history with.
EXPLORE_SHARE = 0.25

#: At most this share of a feed may come from any one channel. Affinity
#: ranking alone hands the whole page to whoever you watch most and uploads
#: most often — which is not a recommendation, it is a subscription box.
CHANNEL_SHARE = 0.2


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

        # Where it came from is worth saying when it came from nowhere the
        # user has been: "you follow them" and "this is new to you" are
        # different sentences and only one of them is true here.
        if self.candidate.source in ("discover", "similar", "shorts") and name == "novelty":
            return ("Like a channel you follow" if self.candidate.source == "similar"
                    else "Something new, about what you watch")

        return {
            "affinity": f"You listen to {artist}",
            "following": f"You follow {artist}",
            "liked": f"You liked something by {artist}",
            "wanted": "Matches an interest you set",
            "discovered": "New to you, about what you watch",
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

        # Watch time, not clicks. YouTube's ranker optimises expected watch
        # time precisely because click-through rewards whatever is most
        # tempting to start, which is not the same as what is worth finishing.
        # Skips already carry their own negative weight, so only plays and
        # likes are scaled.
        if weight > 0:
            weight *= max(0.25, signal.completion or 1.0)

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
    interests=None,
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

    if candidate.source in DISCOVERED:
        terms["discovered"] = weights["discovered"]

    if interests is not None:
        matched = interests.wants(title=candidate.title, channel=candidate.artist)
        if matched:
            terms["wanted"] = weights["wanted"] * min(3.0, matched)

    return Scored(candidate=candidate, score=sum(terms.values()), terms=terms)


def rank(
    candidates: Iterable[Candidate],
    tastes: Tastes,
    *,
    limit: int = 50,
    explore: float = EXPLORE_SHARE,
    channel_share: float = CHANNEL_SHARE,
    weights: Optional[dict[str, float]] = None,
    now: Optional[datetime] = None,
    interests=None,
    repeats: bool = False,
) -> list[Scored]:
    """Order candidates by score, with room reserved for the unfamiliar.

    Dislikes are dropped outright rather than scored low: "not this" should mean
    not this, not "this, further down". Anything already watched is dropped for
    the same reason — `repeats=True` puts it back, for callers that are ordering
    a library rather than recommending from one.

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

    # Everything already watched, dropped outright. The novelty term ranks a
    # seen video lower but still shows it, which is not what "recommend" means
    # — and it is doubly odd here, because the feed is *grown from* things
    # already watched and would otherwise hand them straight back.
    already: set[str] = set()
    if not repeats:
        already = {s.id for s in tastes.signals
                   if s.id and s.kind in ("play", "like", "skip")}

    # Blocked topics are removed before anything is scored. "I never want this"
    # that quietly means "less of this" is the thing everybody hates about
    # recommendation feeds, so it is an exclusion rather than a weight.
    if interests is None:
        interests = getattr(tastes, "interests", None)

    seen: set[str] = set()
    scored: list[Scored] = []

    for candidate in candidates:
        if not candidate.id or candidate.id in seen:
            continue
        if candidate.id in disliked or candidate.id in already:
            continue
        if interests is not None and interests.blocks(
                title=candidate.title, channel=candidate.artist,
                channel_id=candidate.channel_id):
            continue

        seen.add(candidate.id)
        scored.append(score(
            candidate, tastes, affinities=affinities, plays=plays,
            likes=likes, weights=weights, now=now, interests=interests,
        ))

    scored.sort(key=lambda s: s.score, reverse=True)
    scored = drop_near_duplicates(scored)

    # Variety first, and it settles the running order. Doing this before the
    # explore split matters: the split reserves most of the feed for things you
    # already listen to, and if only one channel qualifies then that reservation
    # *requires* one channel to fill three quarters of the page. The channel cap
    # has to win that argument, so it is applied to the whole pool first and
    # nothing re-sorts by score afterwards.
    spread = _spread_channels(scored, limit, channel_share)
    if len(spread) <= limit:
        return spread

    #: Room held for things with no history at all. Taken by walking the spread
    #: rather than by picking two lists and merging them — merging re-admits
    #: exactly the items the channel cap just pushed to the back, which is how
    #: one channel climbed back into the top of the feed.
    room = max(0, int(limit * explore))
    allowance = limit - room

    chosen: list[Scored] = []
    taken_familiar = 0

    for item in spread:
        if len(chosen) >= limit:
            break
        if item.terms.get("affinity") or item.terms.get("following"):
            if taken_familiar >= allowance:
                continue
            taken_familiar += 1
        chosen.append(item)

    if len(chosen) < limit:
        # Not enough unfamiliar candidates to fill the reserved room; give the
        # slots back rather than returning a short feed.
        # Membership by id rather than by object: `Scored` is a dataclass, so
        # `in` compares its candidate and its terms dict field by field, which
        # turns topping up the list into a quadratic crawl.
        taken = {item.candidate.id for item in chosen}
        for item in spread:
            if item.candidate.id not in taken:
                chosen.append(item)
                taken.add(item.candidate.id)
            if len(chosen) >= limit:
                break

    return chosen[:limit]


# ── Keeping a feed between runs ───────────────────────────────────

def feed_path():
    from rose_bouquet.core.library import data_dir

    return data_dir() / "feed.json"


def save_feed(ranked: list[Scored], path=None) -> None:
    """Write the feed out so the next launch has something to show.

    A feed that lives only in memory is an empty page every time the app
    starts, and building one costs half a minute of network — so the page sat
    blank until you knew to press a button and wait. Ranking is cheap and
    re-runnable; *gathering* is not, which is what makes this worth storing.
    """
    from dataclasses import asdict

    payload = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "items": [
            {"candidate": asdict(item.candidate), "score": item.score,
             "terms": dict(item.terms)}
            for item in ranked
        ],
    }

    target = path or feed_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        logger.debug("could not save the feed: %s", exc)


def load_feed(path=None) -> tuple[list[Scored], str]:
    """The last feed and when it was built. ([], "") if there is not one."""
    target = path or feed_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], ""

    if not isinstance(payload, dict):
        return [], ""

    fields = set(Candidate.__dataclass_fields__)
    ranked: list[Scored] = []

    for row in payload.get("items") or []:
        if not isinstance(row, dict):
            continue
        data = row.get("candidate")
        if not isinstance(data, dict):
            continue
        # Filtered rather than splatted: a feed saved by an older version may
        # name fields this one no longer has.
        candidate = Candidate(**{k: v for k, v in data.items() if k in fields})
        terms = row.get("terms")
        ranked.append(Scored(
            candidate=candidate,
            score=float(row.get("score") or 0.0),
            terms=terms if isinstance(terms, dict) else {},
        ))

    return ranked, str(payload.get("at") or "")


def drop_near_duplicates(scored: list, keep: int = 1) -> list:
    """Remove things that are the same thing again.

    A related-video lookup on one video returns a dozen reuploads of it, and
    "Lets get right into the news" four times is not a feed. Titles are
    compared by their meaningful words rather than their exact text, so
    reuploads, mirrors and slightly-renamed copies collapse together.
    """
    from rose_bouquet.core.interests import words

    kept: list = []
    signatures: list[set] = []

    for item in scored:
        candidate = getattr(item, "candidate", item)
        signature = set(words(candidate.title))
        if not signature:
            kept.append(item)
            continue

        # Compared by overlap rather than by an exact key. The same clip is
        # reposted as "Lets get right into the news", "Keemstar - Lets get
        # right into the news" and "Lets Get Right Into The News (Greenscreen)"
        # — no fixed key catches those three, but they share most of their
        # words with each other and almost none with anything else.
        duplicate = False
        for previous in signatures:
            shared = len(signature & previous)
            smaller = min(len(signature), len(previous))
            # At least three words in common as well as a high proportion:
            # on a two-word title any overlap at all looks like a match, and
            # "Zelda speedrun guide" and "Zelda speedrun record" are two
            # different videos.
            if shared >= SAME_TITLE_WORDS and smaller and shared / smaller >= SAME_TITLE:
                duplicate = True
                break

        if duplicate:
            continue

        signatures.append(signature)
        kept.append(item)

    return kept


def _spread_channels(scored: list[Scored], limit: int, share: float) -> list[Scored]:
    """Interleave channels, so no one uploader owns the top of the feed.

    Capping a channel's total is not enough on its own: with a cap of twelve,
    the channel you watch most simply takes the first twelve slots and the page
    still opens as one creator. So this goes round by round instead — every
    channel's best item, then every channel's second best — which is why a feed
    reads as a feed and a subscription box does not.

    Within a round the order is still by score, and anything past a channel's
    cap goes to the back rather than being dropped. Nothing is lost, only
    reordered.
    """
    if share <= 0 or share >= 1:
        return scored

    cap = max(1, int(limit * share))

    by_channel: dict[str, list[Scored]] = {}
    unattributed: list[Scored] = []

    for item in scored:
        key = item.candidate.channel_id or item.candidate.artist.lower()
        if key:
            by_channel.setdefault(key, []).append(item)
        else:
            unattributed.append(item)

    spread: list[Scored] = []
    overflow: list[Scored] = []

    # `scored` arrives sorted, so each channel's list is already best-first.
    depth = max((len(items) for items in by_channel.values()), default=0)
    for position in range(depth):
        round_items = [
            items[position] for items in by_channel.values() if len(items) > position
        ]
        round_items.sort(key=lambda s: s.score, reverse=True)
        if position < cap:
            spread.extend(round_items)
        else:
            overflow.extend(round_items)

    return spread + unattributed + overflow


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


#: Seeds are drawn from at most this many items per channel, so that a run of
#: twenty videos from one channel cannot claim the whole feed.
SEEDS_PER_CHANNEL = 1


def seeds(tastes: Tastes, limit: int = 8, now: Optional[datetime] = None,
          forms: Optional[tuple[str, ...]] = None) -> list[Signal]:
    """The items worth asking YouTube for related tracks to.

    This is candidate *generation*, and it is the stage that decides whether the
    feed has anything in it at all. YouTube's own recommender grows its
    candidates from the watch history — and specifically predicts the **next**
    watch from the recent sequence rather than treating history as an
    undifferentiated bag — so that is what this does: the strongest recent
    signals win, decayed by age.

    The rule this replaces asked for three plays of the same video or an
    explicit like. Both are things an *imported* history can never contain —
    Takeout records a video once, and carries no likes — so a freshly imported
    profile of ten thousand videos produced no seeds whatsoever and grew no
    feed. Requiring a kind of evidence the data cannot hold is not a high bar,
    it is an impossible one.
    """
    from rose_bouquet.core.interests import derive_topics
    from rose_bouquet.core.interests import words as interests_words

    plays = heard(tastes)
    common = {word for word, _ in derive_topics(tastes, limit=30, forms=forms)}

    channel_watches: dict[str, int] = {}
    for signal in tastes.signals:
        if signal.channel_id and signal.kind in ("play", "like"):
            channel_watches[signal.channel_id] = (
                channel_watches.get(signal.channel_id, 0) + 1)

    considered = tastes.signals
    if forms:
        # Seeded from one appetite at a time: a video feed grown out of what
        # somebody flicked through in a shorts reel is a video feed full of
        # things they never chose to sit down and watch.
        considered = [s for s in considered
                      if (getattr(s, "form", "video") or "video") in forms]

    weighted: list[tuple[float, Signal]] = []
    for signal in considered:
        if signal.kind in ("dislike", "skip"):
            continue

        strength = 3.0 if signal.kind == "like" else 1.0
        # Watch time over clicks, the central lesson of YouTube's ranker: half
        # -watched is half the evidence. Imported history has no completion
        # recorded and defaults to 1.0, which is the right assumption for
        # something that reached the history at all.
        strength *= max(0.25, signal.completion or 1.0)
        # Replays are a real endorsement, just no longer a requirement.
        strength *= 1.0 + 0.3 * max(0, plays.get(signal.id, 0) - 1)

        # Corroboration: a subject that turns up again and again in somebody's
        # history is what they are actually interested in, where a thing
        # watched once may have been a mistake, a link from a friend, or a
        # commercial clothes press at two in the morning. Both are kept — the
        # one-off just does not get to be the top seed.
        recurring = sum(1 for word in set(interests_words(signal.title))
                        if word in common)
        strength *= 1.0 + min(1.5, recurring * 0.5)

        # And by the channel it came from. A word can recur for the wrong
        # reason — "steam" is both a games shop and a laundry appliance, and
        # one watch of the latter scored as highly as a hundred of the former.
        # How often somebody returns to a *channel* has no such ambiguity.
        strength *= 1.0 + min(2.0, channel_watches.get(signal.channel_id, 0) * 0.05)

        weighted.append((strength * decay(signal.at, now), signal))

    weighted.sort(key=lambda pair: pair[0], reverse=True)

    chosen: list[Signal] = []
    seen: set[str] = set()
    per_channel: dict[str, int] = {}

    for _strength, signal in weighted:
        if signal.id in seen:
            continue

        # Without this the top seeds are all the same channel, every `related`
        # call returns more of it, and the feed is one creator wide.
        channel = signal.channel_id or signal.artist.lower()
        if channel and per_channel.get(channel, 0) >= SEEDS_PER_CHANNEL:
            continue

        seen.add(signal.id)
        if channel:
            per_channel[channel] = per_channel.get(channel, 0) + 1
        chosen.append(signal)

        if len(chosen) >= limit:
            break

    return chosen


def watched_channels(tastes: Tastes, limit: int = 25,
                    now: Optional[datetime] = None,
                    forms: Optional[tuple[str, ...]] = None) -> list:
    """The channels somebody actually watches, most-watched first.

    This is the strongest signal a local recommender has, and it was sitting
    unused: a watch history names the channels *and* says how many times each
    was chosen. Subscriptions do not — a subscription list is a record of
    decisions made once, sometimes years ago, and an imported one may be four
    hundred channels the person no longer watches.
    """
    from rose_bouquet.core.tastes import Channel

    scores: dict[str, float] = {}
    names: dict[str, str] = {}

    signals = tastes.signals
    if forms:
        signals = [s for s in signals
                   if (getattr(s, "form", "video") or "video") in forms]

    for signal in signals:
        if not signal.channel_id:
            continue

        weight = {"like": 3.0, "play": 1.0, "skip": -1.5, "dislike": -4.0}.get(
            signal.kind, 0.0)
        if not weight:
            continue
        if weight > 0:
            weight *= max(0.25, signal.completion or 1.0)

        scores[signal.channel_id] = scores.get(signal.channel_id, 0.0) + (
            weight * decay(signal.at, now))
        if signal.artist:
            names.setdefault(signal.channel_id, signal.artist)

    muted = {c.id for c in tastes.channels.values() if c.muted}
    ranked = sorted(
        ((cid, score) for cid, score in scores.items()
         if score > 0 and cid not in muted),
        key=lambda pair: pair[1], reverse=True,
    )

    return [Channel(id=cid, title=names.get(cid, ""), kind="channel")
            for cid, _score in ranked[:limit]]


def feed_channels(
    tastes: Tastes,
    limit: int = 40,
    *,
    now: Optional[datetime] = None,
    rng=None,
) -> list:
    """Which subscriptions to actually check when building a feed.

    Checking every subscription means one network round trip per channel, and
    at four hundred channels that is minutes of waiting before anything appears
    — long enough that the feed reads as broken rather than slow.

    So this narrows first, which is exactly what a candidate generation stage is
    for. Two thirds go to the channels you actually watch, ranked by affinity;
    the rest is a random sample of everything else, so a channel you have never
    watched still gets its turn and the feed is different each time it is built.
    """
    import random

    channels = [c for c in tastes.subscriptions() if not c.muted]
    if len(channels) <= limit:
        return channels

    strength = affinity(tastes, now)
    ranked = sorted(
        channels,
        key=lambda c: max(strength.get(c.id, 0.0), strength.get(c.title.lower(), 0.0)),
        reverse=True,
    )

    familiar = max(1, (limit * 2) // 3)
    chosen = ranked[:familiar]

    rest = ranked[familiar:]
    room = limit - len(chosen)
    if rest and room > 0:
        chosen.extend((rng or random).sample(rest, min(room, len(rest))))

    return chosen
