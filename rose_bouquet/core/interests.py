"""What you are interested in — worked out, and told.

The feeds used to be subscription boxes: everything came from channels already
followed, so they showed the same dozen creators forever and ran dry. This is
the other half — the part that finds things you have never seen.

**Topics are words.** There is no classifier here and no model. A video's
subject is taken from the words in its title and its hashtags, and yours from
the words in the titles of things you actually watched. Two things are related
when they share words. That is cruder than what a streaming service does with
a hundred million viewers' co-watch data, and it has one large compensating
advantage: you can read it, see exactly why something was suggested, and
change it by typing.

**Stated interests outrank derived ones.** Anything you type in is worth more
than anything inferred, because you know what you want and this file is only
guessing. And a blocked topic is a hard exclusion, not a nudge — "I don't want
this" that quietly means "less of this" is the thing everybody hates about
recommendation feeds.

On what YouTube does with Shorts, since this imitates it: the Shorts feed is a
discovery feed rather than a subscription one, ranked on how much of a thing
you watched and whether you replayed it rather than on whether you clicked,
and it deliberately avoids putting the same creator back to back. All three
are reproduced here — see `recommend.py` for the ranking and the channel
spreading, and `WATCH_WEIGHT` below for the first.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

#: Words that say nothing about a subject. Deliberately short: this is a stop
#: list for *titles*, which are already terse, not for prose.
STOPWORDS = {
    "a", "об", "the", "and", "or", "but", "if", "of", "to", "in", "on", "for",
    "with", "at", "by", "from", "up", "out", "is", "it", "its", "this", "that",
    "these", "those", "as", "be", "was", "were", "are", "am", "been", "you",
    "your", "yours", "my", "me", "we", "our", "i", "he", "she", "they", "them",
    "his", "her", "their", "how", "what", "why", "when", "where", "who",
    "which", "new", "best", "top", "vs", "ft", "feat", "official", "video",
    "music", "audio", "lyrics", "full", "part", "episode", "ep", "live",
    "shorts", "short", "youtube", "subscribe", "like", "watch", "now", "get",
    "make", "made", "makes", "did", "do", "does", "not", "no", "yes", "all",
    "one", "two", "more", "most", "just", "about", "can", "will", "so",
    # Fillers that survive the obvious list and dominate any frequency count
    # of real titles — measured against a real watch history, where "ever",
    # "situation" and "huge" outranked every actual subject.
    "ever", "never", "time", "times", "situation", "huge", "got",
    "going", "goes", "went", "really", "actually", "literally", "every",
    "everything", "something", "nothing", "anything", "someone", "people",
    "guy", "guys", "man", "thing", "things", "way", "ways", "day", "days",
    "year", "years", "first", "last", "next", "again", "still", "back",
    "into", "over", "after", "before", "than", "then", "there", "here",
    "much", "many", "little", "big", "old", "good", "bad", "better",
    "worse", "worst", "crazy", "insane", "amazing", "wtf", "omg", "lol",
    "part1", "pt", "vol", "hd", "4k", "60fps", "reaction", "review",
}

#: Words shorter than this carry no meaning on their own — "up", "go", "hd".
MIN_WORD = 3

#: How many derived topics to keep. Enough to cover a varied taste, few enough
#: that the tail is not noise from one odd evening.
TOPIC_LIMIT = 24

#: A watched thing counts for this much when deriving topics; a liked one for
#: more, and a skipped one against. This is the watch-percentage idea the
#: Shorts feed is built on: finishing something says far more than starting it.
WATCH_WEIGHT = {"like": 3.0, "play": 1.0, "skip": -2.0, "dislike": -4.0}

#: Things nobody asked to be shown. Not a morality filter and not a quality
#: judgement on any creator — it is a list of the words that turn up in the
#: two kinds of thing a topic search drags in that nobody wants: engagement
#: bait aimed at nobody in particular, and bulk-generated filler.
#:
#: On the wide side deliberately. A feed that occasionally hides something
#: harmless is a smaller problem than one that shows this, and anything caught
#: unfairly can be unblocked by name in Settings.
SLOP = {
    # Bulk-generated filler.
    "ai generated", "ai-generated", "made with ai", "ai cover", "ai voice",
    "ai music", "text to speech", "tts story", "reddit stories",
    "chatgpt", "veo", "sora", "midjourney", "elevenlabs",
    # Engagement bait with no subject.
    "gone wrong", "gone sexual", "you won't believe", "wont believe",
    "shocking truth", "must watch", "watch till the end", "wait for it",
    "part 999", "satisfying video", "oddly satisfying",
    # Suggestive filler aimed at nobody in particular.
    "hot girl", "hot girls", "sexy", "thicc", "onlyfans", "nsfw",
    "gooner", "gooning", "edging", "waifu ranking", "tier list of girls",
}

_WORD = re.compile(r"[a-z0-9]+")
_HASHTAG = re.compile(r"#(\w+)")


def words(text: str) -> list[str]:
    """The meaningful words in a title, lowercased.

    Hashtags are kept without their hash — `#skateboarding` and
    `skateboarding` are the same subject, and a feed that treats them as two
    would show you both and think it had shown you variety.
    """
    if not text:
        return []

    # Apostrophes dropped rather than kept: "let's" and "lets" are the same
    # word, and a reupload that punctuates its title differently is the same
    # video. Keeping them apart defeats the de-duplication entirely.
    lowered = text.lower().replace("'", "").replace("\u2019", "")
    found = [tag for tag in _HASHTAG.findall(lowered)]
    found += _WORD.findall(lowered)

    return [
        word for word in found
        if len(word) >= MIN_WORD and word not in STOPWORDS and not word.isdigit()
    ]


@dataclass
class Interests:
    """What to look for, and what never to show.

    All three lists are plain strings the user can read and edit. Nothing here
    is a hidden score.
    """

    #: Topics asked for by name. These outrank anything derived.
    wanted: list[str] = field(default_factory=list)
    #: Topics never to show. A hard exclusion.
    blocked: list[str] = field(default_factory=list)
    #: Channels never to show, by name or id.
    blocked_channels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "wanted": list(self.wanted),
            "blocked": list(self.blocked),
            "blocked_channels": list(self.blocked_channels),
            "filter_slop": self.filter_slop,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Interests":
        if not isinstance(data, dict):
            return cls()

        def strings(key: str) -> list[str]:
            value = data.get(key)
            if not isinstance(value, list):
                return []
            return [v.strip() for v in value if isinstance(v, str) and v.strip()]

        return cls(
            wanted=strings("wanted"), blocked=strings("blocked"),
            blocked_channels=strings("blocked_channels"),
            filter_slop=bool(data.get("filter_slop", True)),
        )

    # ── Asking about one thing ────────────────────────────────────

    #: Whether the built-in slop list applies as well as the user's own.
    filter_slop: bool = True

    def blocks(self, title: str = "", channel: str = "",
               channel_id: str = "") -> bool:
        """Whether this is something that should never be shown.

        Matched on whole words rather than substrings: blocking "war" should
        not also hide "warm", "software" and "Warsaw".
        """
        if self.filter_slop and is_slop(title):
            return True

        for name in self.blocked_channels:
            wanted = name.strip().lower()
            if wanted and wanted in (channel.lower(), channel_id.lower()):
                return True

        if not self.blocked:
            return False

        present = set(words(title)) | set(words(channel))
        return any(
            all(part in present for part in words(topic))
            for topic in self.blocked if words(topic)
        )

    def wants(self, title: str = "", channel: str = "") -> float:
        """How well this matches a stated interest, 0.0 upwards."""
        if not self.wanted:
            return 0.0

        present = set(words(title)) | set(words(channel))
        if not present:
            return 0.0

        score = 0.0
        for topic in self.wanted:
            parts = words(topic)
            if parts and all(part in present for part in parts):
                # A two-word interest matching in full is a stronger signal
                # than a one-word one, so it is worth more.
                score += len(parts)
        return score


def is_slop(title: str) -> bool:
    """Whether a title is one of the things a topic search drags in.

    Phrases are matched as phrases — "hot girl" rather than "hot" — because
    single words catch far too much: "hot" is in half the cooking videos ever
    made, and a filter that hides those is a filter people turn off.
    """
    if not title:
        return False

    lowered = f" {title.lower()} "
    return any(f" {phrase} " in lowered or lowered.strip().startswith(f"{phrase} ")
               for phrase in SLOP)


def derive_topics(tastes, limit: int = TOPIC_LIMIT,
                  forms: Optional[tuple[str, ...]] = None) -> list[tuple[str, float]]:
    """The subjects somebody actually watches, from their own history.

    Weighted by what they did: a like counts for three, a play for one, and a
    skip counts *against* — the strongest signal a short-form feed has is
    somebody bailing out, and a topic that keeps getting skipped should stop
    appearing rather than merely appear less.
    """
    scores: Counter[str] = Counter()

    signals = getattr(tastes, "signals", [])
    if forms:
        # Videos and shorts are different appetites. What somebody flicks
        # through at one in the morning should not decide what their video
        # feed shows them tomorrow.
        signals = [s for s in signals if (getattr(s, "form", "video") or "video") in forms]

    for signal in signals:
        weight = WATCH_WEIGHT.get(signal.kind, 0.0)
        if not weight:
            continue

        # Watch percentage, where it is known. Finishing something says far
        # more than starting it.
        if weight > 0:
            weight *= max(0.25, getattr(signal, "completion", 1.0) or 1.0)

        for word in set(words(signal.title)):
            scores[word] += weight

    return [(word, score) for word, score in scores.most_common() if score > 0][:limit]


def search_terms(tastes, interests: Optional[Interests] = None,
                 limit: int = 8,
                 forms: Optional[tuple[str, ...]] = None) -> list[str]:
    """What to actually go and search for.

    Stated interests first and in full, then pairs of the strongest derived
    topics. Pairs rather than single words on purpose: searching "guitar"
    returns everything ever made, while "guitar pedal" returns something
    somebody might actually want.
    """
    interests = interests or Interests()
    terms: list[str] = []
    seen: set[str] = set()

    for topic in interests.wanted:
        cleaned = topic.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            terms.append(cleaned)

    # Pairs taken from words that actually appeared in the *same* title, not
    # from neighbouring positions in a frequency table. Ranking two unrelated
    # subjects next to each other and gluing them together produces searches
    # like "situation wii", which finds nothing and describes nobody.
    derived = derive_topics(tastes, forms=forms)
    strong = {word for word, _ in derived}
    together = _co_occurring(tastes, strong)

    for pair, _count in together:
        phrase = " ".join(pair)
        if phrase.lower() not in seen:
            seen.add(phrase.lower())
            terms.append(phrase)

    # A lone strong topic is better than nothing when there is no history to
    # pair up and nothing was asked for.
    for word, _score in derived:
        if len(terms) >= limit:
            break
        if word.lower() not in seen:
            seen.add(word.lower())
            terms.append(word)

    return terms[:limit]


def _co_occurring(tastes, strong: set[str], limit: int = 12) -> list[tuple[tuple[str, str], int]]:
    """Pairs of strong topics that turn up in the same title.

    Two words a person's own titles put together are a phrase that describes
    something real — "ocarina time", "tf2 update" — where two words merely
    ranked next to each other describe nothing.
    """
    pairs: Counter[tuple[str, str]] = Counter()

    for signal in getattr(tastes, "signals", []):
        if WATCH_WEIGHT.get(signal.kind, 0.0) <= 0:
            continue

        present = sorted({word for word in words(signal.title) if word in strong})
        for first in range(len(present)):
            for second in range(first + 1, len(present)):
                pairs[(present[first], present[second])] += 1

    return [pair for pair in pairs.most_common(limit) if pair[1] > 1]


def keep(candidates: Iterable, interests: Optional[Interests] = None) -> list:
    """Drop everything the user said they never want to see."""
    interests = interests or Interests()
    return [
        candidate for candidate in candidates
        if not interests.blocks(
            title=getattr(candidate, "title", ""),
            channel=getattr(candidate, "artist", "") or getattr(candidate, "channel", ""),
            channel_id=getattr(candidate, "channel_id", ""),
        )
    ]
