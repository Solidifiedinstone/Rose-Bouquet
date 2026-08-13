"""Finding the picture for a track, wherever it happens to be kept.

Cover art hides in three different places and a music player has to look in all
of them: a `cover.jpg` sitting next to the file, a picture frame embedded in the
tags, or — for anything that came from YouTube — a thumbnail on YouTube's own
servers. `library.find_cover` only ever knew about the first, which is why a
downloaded album with its art tucked inside the tags showed up blank.

Embedded art is the awkward one: it is bytes inside a container, and nothing
outside this process can be handed bytes. Desktop media controls want a *URL*.
So embedded pictures are written out once to a cache file and handed over as
`file://`, keyed on the track's path and modification time — retag a file and
the key changes, so the stale picture is never served.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from rose_bouquet.core.library import data_dir

logger = logging.getLogger(__name__)

#: Cover formats worth writing out, and what to call the file.
_SUFFIXES = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/png": ".png", "image/webp": ".webp",
}


def cache_dir() -> Path:
    folder = data_dir() / "covers"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def youtube_thumbnail(video_id: str) -> str:
    """YouTube's own thumbnail for a video id.

    The same stable public url the rest of the app uses for thumbnails, at the
    largest size that always exists — `maxresdefault` is often missing, and a
    404 in a media widget shows as a broken picture rather than no picture.
    """
    if not video_id:
        return ""
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def embedded_art(path: str) -> str:
    """Extract the picture inside a file's tags, cached. Path, or "" if none.

    Never raises: a file with a corrupt picture frame is still a file that
    should play.
    """
    source = Path(path)
    try:
        stat = source.stat()
    except OSError:
        return ""

    key = hashlib.sha1(f"{source}:{stat.st_mtime_ns}".encode()).hexdigest()[:20]  # noqa: S324

    for suffix in (".jpg", ".png", ".webp"):
        cached = cache_dir() / f"{key}{suffix}"
        if cached.exists():
            return str(cached)

    #: A file whose tags hold no picture is remembered too, so that every
    #: track change does not re-parse an entire FLAC to learn "still nothing".
    miss = cache_dir() / f"{key}.none"
    if miss.exists():
        return ""

    data, mime = _read_embedded(source)
    if not data:
        try:
            miss.touch()
        except OSError:
            pass
        return ""

    target = cache_dir() / f"{key}{_SUFFIXES.get(mime, '.jpg')}"
    try:
        target.write_bytes(data)
    except OSError as exc:
        logger.debug("could not cache cover for %s: %s", source, exc)
        return ""
    return str(target)


def _read_embedded(path: Path) -> tuple[bytes, str]:
    """The first picture in a file's tags, as (bytes, mime type).

    Every container stores pictures differently and mutagen reflects that
    faithfully rather than papering over it, so each has to be asked in its own
    terms.
    """
    try:
        import mutagen

        audio = mutagen.File(str(path))
    except Exception as exc:                # noqa: BLE001 — artwork is never worth raising over
        logger.debug("could not read %s: %s", path, exc)
        return b"", ""

    if audio is None:
        return b"", ""

    # FLAC, and anything else exposing real Picture blocks.
    pictures = getattr(audio, "pictures", None)
    if pictures:
        picture = pictures[0]
        return bytes(picture.data), (picture.mime or "image/jpeg")

    tags = getattr(audio, "tags", None)
    if tags is None:
        return b"", ""

    # ID3 — MP3, and AIFF/WAV carrying ID3.
    try:
        frames = tags.getall("APIC")
    except AttributeError:
        frames = []
    if frames:
        return bytes(frames[0].data), (frames[0].mime or "image/jpeg")

    # MP4 / M4A, which names the format instead of giving a mime type.
    covers = None
    try:
        covers = tags.get("covr")
    except (AttributeError, TypeError):
        covers = None
    if covers:
        cover = covers[0]
        fmt = getattr(cover, "imageformat", None)
        mime = "image/png" if fmt == 14 else "image/jpeg"   # MP4Cover.FORMAT_PNG
        return bytes(cover), mime

    # Ogg Vorbis and Opus: a FLAC picture block, base64'd into a comment.
    for field in ("metadata_block_picture", "coverart"):
        try:
            values = tags.get(field)
        except (AttributeError, TypeError):
            values = None
        if not values:
            continue
        import base64

        try:
            raw = base64.b64decode(values[0])
        except Exception:                   # noqa: BLE001
            continue
        if field == "coverart":
            # The older, mime-less convention.
            return raw, "image/jpeg"
        try:
            from mutagen.flac import Picture

            picture = Picture(raw)
            return bytes(picture.data), (picture.mime or "image/jpeg")
        except Exception:                   # noqa: BLE001
            continue

    return b"", ""


def local_art(track) -> str:
    """A picture on disk for this track, or "".

    Sidecar first: a `cover.jpg` the user put there is the art they chose, and
    it is free to find, where reading an embedded picture means parsing the
    file.
    """
    cover = getattr(track, "cover", "")
    if cover and Path(cover).exists():
        return cover

    path = getattr(track, "path", "")
    return embedded_art(path) if path else ""


def art_url(track, *, resolve: Optional[callable] = None) -> str:
    """The track's artwork as a URL, for anything outside this process.

    Local files win; a YouTube track with nothing on disk falls back to the
    thumbnail, which is a real url pointing at a real picture and is what the
    rest of the app shows for that track anyway.
    """
    if track is None:
        return ""

    local = (resolve or local_art)(track)
    if local:
        return Path(local).as_uri()

    if getattr(track, "source", "") == "youtube" or getattr(track, "source_id", ""):
        return youtube_thumbnail(getattr(track, "source_id", ""))

    return ""
