"""Importing your YouTube history and subscriptions from Google Takeout.

The feed is only as good as what it knows about you, and a fresh install knows
nothing. You already have years of it — Google keeps it, and Takeout will hand
it over: `takeout.google.com` → YouTube → watch history and subscriptions.

That is the whole point of doing it this way. No account is connected, no OAuth
prompt, no token that keeps working after you have forgotten about it. You
export a file, it is read once, and what comes out lands in the same local
profile as everything else — where you can inspect it, edit it, or delete it.

Handles what Takeout actually produces:

  - `watch-history.json` — the JSON export, with titles, channels and times.
  - `watch-history.html` — the HTML export, which is what you get if you forget
    to change the format. Parsed too, because "export it again, differently" is
    a miserable thing to be told.
  - `subscriptions.csv` — channel id, url, title.
  - A `.zip` straight from Takeout, unopened.

Everything is best-effort: a row that cannot be read is skipped, not fatal.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path

logger = logging.getLogger(__name__)

VIDEO_ID = re.compile(r"(?:watch\?v=|youtu\.be/|/shorts/)([A-Za-z0-9_\-]{11})")
CHANNEL_ID = re.compile(r"channel/(UC[A-Za-z0-9_\-]{20,})")

#: HTML export: each entry is a cell with links and a trailing date.
HTML_ENTRY = re.compile(
    r'<div class="content-cell[^"]*">(.*?)</div>', re.S | re.I
)
HTML_LINK = re.compile(r'<a href="([^"]+)">([^<]*)</a>', re.S)

#: The date at the end of an HTML cell: "Aug 12, 2026, 5:16:39 PM PDT".
#: The trailing zone abbreviation is deliberately left out of the capture —
#: `%Z` only knows a couple of them, and an hour's error either way means
#: nothing to a decay measured in months.
HTML_DATE = re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s*[AP]M)"
)
HTML_DATE_FORMATS = ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y, %H:%M:%S")

#: Watch history can be enormous. Only the most recent entries say anything
#: useful about current taste, and reading 200,000 rows to weight them at
#: nearly zero is a waste of everyone's time.
HISTORY_LIMIT = 5000


@dataclass
class Watched:
    """One thing you watched."""

    video_id: str = ""
    title: str = ""
    channel: str = ""
    channel_id: str = ""
    at: str = ""


@dataclass
class Subscription:
    channel_id: str = ""
    title: str = ""


@dataclass
class TakeoutData:
    """What came out of an export."""

    watched: list[Watched] = field(default_factory=list)
    subscriptions: list[Subscription] = field(default_factory=list)
    #: Anything that was found but could not be read, for honesty.
    skipped: int = 0

    @property
    def summary(self) -> str:
        parts = []
        if self.watched:
            parts.append(f"{len(self.watched)} watched videos")
        if self.subscriptions:
            parts.append(f"{len(self.subscriptions)} subscriptions")
        return " and ".join(parts) if parts else "nothing usable"


def _clean_title(title: str) -> str:
    """Takeout prefixes history entries with "Watched "."""
    # The HTML export is HTML, so its titles carry entities: without this,
    # every apostrophe in the feed reads as "It&#39;s".
    title = html.unescape(title)

    for prefix in ("Watched ", "Viewed ", "Watched a video that has been removed"):
        if title.startswith(prefix):
            return title[len(prefix):].strip()
    return title.strip()


def parse_watch_history_json(text: str, limit: int = HISTORY_LIMIT) -> list[Watched]:
    """The JSON watch history export."""
    try:
        rows = json.loads(text)
    except ValueError as exc:
        logger.warning("watch history is not readable JSON: %s", exc)
        return []

    if not isinstance(rows, list):
        return []

    watched: list[Watched] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue

        url = row.get("titleUrl") or ""
        match = VIDEO_ID.search(url)
        if not match:
            # Removed videos, ads, and survey rows have no watchable id.
            continue

        subtitles = row.get("subtitles")
        channel, channel_id = "", ""
        if isinstance(subtitles, list) and subtitles:
            first = subtitles[0]
            if isinstance(first, dict):
                channel = first.get("name", "")
                found = CHANNEL_ID.search(first.get("url", "") or "")
                channel_id = found.group(1) if found else ""

        watched.append(Watched(
            video_id=match.group(1),
            title=_clean_title(row.get("title", "")),
            channel=channel,
            channel_id=channel_id,
            at=str(row.get("time") or ""),
        ))

    return watched


def _html_date(cell: str) -> str:
    """The watch date out of an HTML cell, as ISO. "" if it cannot be read.

    Without this every imported video is stamped with the moment of import, and
    a decade of history all lands on today — which tells the feed that a video
    watched in 2019 is as current as one watched this morning.
    """
    # Takeout separates the time from AM/PM with a narrow no-break space, and
    # `strptime` will not match that against a plain space in the format.
    text = cell.replace("\u202f", " ").replace("\xa0", " ")

    match = HTML_DATE.search(text)
    if not match:
        return ""

    stamp = re.sub(r"\s+", " ", match.group(1)).strip()
    for fmt in HTML_DATE_FORMATS:
        try:
            return datetime.strptime(stamp, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return ""


def parse_watch_history_html(text: str, limit: int = HISTORY_LIMIT) -> list[Watched]:
    """The HTML watch history export, for anyone who forgot to pick JSON."""
    watched: list[Watched] = []

    for cell in HTML_ENTRY.findall(text):
        links = HTML_LINK.findall(cell)
        if not links:
            continue

        video_url, video_title = links[0]
        match = VIDEO_ID.search(video_url)
        if not match:
            continue

        channel, channel_id = "", ""
        if len(links) > 1:
            channel_url, channel = links[1]
            found = CHANNEL_ID.search(channel_url)
            channel_id = found.group(1) if found else ""

        watched.append(Watched(
            video_id=match.group(1),
            title=_clean_title(video_title),
            channel=channel.strip(),
            channel_id=channel_id,
            at=_html_date(cell),
        ))
        if len(watched) >= limit:
            break

    return watched


def parse_subscriptions_csv(text: str) -> list[Subscription]:
    """The subscriptions export.

    Column names have changed between Takeout versions, so they are matched
    loosely rather than by exact header.
    """
    subscriptions: list[Subscription] = []

    try:
        reader = csv.DictReader(StringIO(text))
        for row in reader:
            lowered = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}

            channel_id = (lowered.get("channel id") or lowered.get("channelid")
                          or lowered.get("id") or "")
            title = (lowered.get("channel title") or lowered.get("channeltitle")
                     or lowered.get("title") or lowered.get("name") or "")

            if not channel_id:
                url = lowered.get("channel url") or lowered.get("channelurl") or ""
                found = CHANNEL_ID.search(url)
                channel_id = found.group(1) if found else ""

            if channel_id or title:
                subscriptions.append(Subscription(channel_id=channel_id, title=title))
    except csv.Error as exc:
        logger.warning("subscriptions.csv is not readable: %s", exc)

    return subscriptions


def read(path: Path, *, limit: int = HISTORY_LIMIT) -> TakeoutData:
    """Read an export: a zip, a Takeout folder, or a single exported file."""
    path = Path(path).expanduser()
    data = TakeoutData()

    if not path.exists():
        return data

    if path.is_file() and path.suffix.lower() == ".zip":
        return _read_zip(path, limit=limit)

    if path.is_file():
        _read_one(path.name, _text(path), data, limit)
        return data

    # A folder: find the files wherever Takeout buried them.
    for candidate in sorted(path.rglob("*")):
        if candidate.is_file() and candidate.name.lower() in (
            "watch-history.json", "watch-history.html", "subscriptions.csv",
        ):
            _read_one(candidate.name, _text(candidate), data, limit)

    return data


def _read_zip(path: Path, *, limit: int) -> TakeoutData:
    data = TakeoutData()
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                leaf = name.rsplit("/", 1)[-1].lower()
                if leaf not in ("watch-history.json", "watch-history.html", "subscriptions.csv"):
                    continue
                try:
                    text = archive.read(name).decode("utf-8", "replace")
                except (KeyError, OSError) as exc:
                    logger.warning("could not read %s from the archive: %s", name, exc)
                    data.skipped += 1
                    continue
                _read_one(leaf, text, data, limit)
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("could not open %s: %s", path, exc)

    return data


def _read_one(name: str, text: str, data: TakeoutData, limit: int) -> None:
    lowered = name.lower()
    if lowered == "watch-history.json":
        data.watched.extend(parse_watch_history_json(text, limit))
    elif lowered == "watch-history.html":
        data.watched.extend(parse_watch_history_html(text, limit))
    elif lowered == "subscriptions.csv":
        data.subscriptions.extend(parse_subscriptions_csv(text))


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("could not read %s: %s", path, exc)
        return ""


# ── Applying it to the profile ────────────────────────────────────

def apply(data: TakeoutData, tastes) -> tuple[int, int]:
    """Fold an export into the local profile. Returns (plays added, channels followed).

    History is recorded as plays with their original timestamps, so the decay
    that weights recent listening higher works on imported history exactly as it
    does on listening done here — a video watched in 2019 counts, faintly.

    Nothing is duplicated: a video already in the profile is left alone, and a
    channel already followed keeps its original subscription date.
    """
    from rose_bouquet.core.tastes import Channel, Signal

    known = {signal.id for signal in tastes.signals}
    plays = 0

    for entry in data.watched:
        if entry.video_id in known:
            continue
        known.add(entry.video_id)
        tastes.record(Signal(
            id=entry.video_id,
            title=entry.title,
            artist=entry.channel,
            channel_id=entry.channel_id,
            kind="play",
            at=_stamp(entry.at),
            completion=1.0,
        ))
        plays += 1

    followed = 0
    for subscription in data.subscriptions:
        identifier = subscription.channel_id or subscription.title
        if not identifier or tastes.subscribed(identifier):
            continue
        tastes.subscribe(Channel(
            id=subscription.channel_id or subscription.title,
            title=subscription.title or subscription.channel_id,
            kind="channel",
        ))
        followed += 1

    return plays, followed


def _stamp(value: str) -> str:
    """Takeout's ISO timestamps, normalised; anything unreadable becomes now."""
    if value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
                tzinfo=None).isoformat(timespec="seconds")
        except ValueError:
            pass
    return datetime.now().isoformat(timespec="seconds")
