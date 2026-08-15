"""What is on an album, asked of whoever actually knows.

MusicBrainz is the better answer when it has one: it is a catalogue, its track
numbers are the release's own, and it has no reason to guess. But it is a
catalogue of *published* records, and a library full of netlabel releases,
bandcamp rips and things a friend uploaded is largely not in it. Asked about
`golden dogs` or `Varra II` it simply says no, and the album then looks exactly
as it did before the tracklist feature existed — which is indistinguishable
from the feature being broken.

YouTube Music knows those, because that is where they were published. So it is
asked second, and only when MusicBrainz has nothing.

The reason it is second and not first is that it always answers. Search it for
`Acoustin / Acoustin` and it returns `Black & White (Acoustic)` by Meechi Mono
— a real album, confidently wrong. So its answer is only accepted when both
the artist and the title actually match what was asked for. A tracklist from
the wrong record is worse than no tracklist: it would invent songs you have
never heard of and then offer to download them.
"""

from __future__ import annotations

import logging
from typing import Optional

from rose_bouquet.core import musicbrainz
from rose_bouquet.core.musicbrainz import CatalogueTrack, Release, normalise

logger = logging.getLogger(__name__)


def lookup(artist: str, album: str, ytmusic=None) -> Optional[Release]:
    """The album's tracklist, from the catalogue or from YouTube Music."""
    release = musicbrainz.tracklist(artist, album)
    if release is not None and release.tracks:
        return release

    if ytmusic is None:
        return None

    return _from_ytmusic(artist, album, ytmusic)


def _from_ytmusic(artist: str, album: str, ytmusic) -> Optional[Release]:
    """YouTube Music's idea of the album, if it is plainly the right one."""
    try:
        hits = ytmusic.search(f"{artist} {album}".strip(), "albums", 5)
    except Exception as exc:                          # noqa: BLE001
        logger.info("no YouTube Music album for %r: %s", album, exc)
        return None

    wanted_album, wanted_artist = normalise(album), normalise(artist)

    for hit in hits or []:
        if normalise(getattr(hit, "title", "")) != wanted_album:
            continue
        # An artist mismatch is the failure this guard exists for: searching
        # for one album and being handed a different one, confidently.
        if wanted_artist and normalise(getattr(hit, "artist", "")) != wanted_artist:
            continue

        try:
            title, results = ytmusic.album(getattr(hit, "browse_id", ""))
        except Exception as exc:                      # noqa: BLE001
            logger.info("could not read that YouTube Music album: %s", exc)
            return None

        tracks = [
            CatalogueTrack(position=index, title=result.title,
                           duration=getattr(result, "duration", 0) or 0)
            for index, result in enumerate(results or [], start=1)
            if getattr(result, "title", "")
        ]
        if not tracks:
            return None

        return Release(title=title or album, artist=artist,
                       mbid="", date="", tracks=tracks)

    return None
