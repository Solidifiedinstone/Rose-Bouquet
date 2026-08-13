<div align="center">

```
         _
      _.;_'-._
     {`--.-'_,}
    {; \,__.-'/}
    {.'-`._;-';
     `'--._.-'
        .-\\,-"-.
        `- \( '-. \
            \;---,/
        .-""-;\
       /  .-' )\
       \,---'` \\
                \|
```

# Rose Bouquet

**Everything you listen to, and everything you follow.**

A music player, a YouTube client, and a music server for your own network —
with no account anywhere, and an algorithm that runs on your machine and tells
you what it is doing.

</div>

---

## What it does

**For you** — a feed built here, from what you follow and what you actually
play. Every item says *why* it is there: "You follow Warp Records", "You liked
something by Boards of Canada", "You have not heard this yet". A quarter of the
feed is reserved for things you have no history with, so it cannot collapse into
the same five artists. There is no account: the profile is one JSON file you
own, and deleting it deletes the algorithm's opinion of you, completely.

**Watch** — search YouTube and play video in the app, with a real transport.
Switch to audio-only and keep your position. Download the audio, follow the
channel, or like it from the player. Starting a video pauses the music rather
than playing both at once.

**Following** — subscriptions kept locally rather than on an account. Paste a
channel link, or follow anything you are watching. Mute a channel to keep
following it without seeing it in the feed.

**Library** — your own files, scanned from folders you choose, with tags read
by mutagen. Albums group properly, including compilations. Shuffle is a
shuffled *order* rather than a random pick, so nothing repeats before the album
is through and "previous" means something.

**Playlists** — extended M3U files in a folder, readable by every other player
ever written.

**Import from Spotify** — brings the track list across and finds each song on
YouTube Music. The ones it *cannot* find are listed and saved with the playlist
rather than quietly dropped. An import is a record on disk, so an interrupted
one carries on where it stopped, nothing downloads twice, and a part-read
playlist tops up the same record.

**Serve** — hosts your library over the local network using the Subsonic API,
so the phone in your pocket can play it, and so can Symfonium, DSub,
substreamer or Feishin. Byte-range streaming, so seeking works.

**The visualiser** — cava, driven with the exact settings from your Quickshell
config (waves, 50 bars, 60fps, mono average, noise reduction 20) and smoothed
the same way, so the player and the desktop bar draw the same numbers. Wave,
bars, or mirrored.

## Appearance

25 themes and 11 styles, and any theme composes with any style — the same
palettes as Rose GameLab and Rose Productivity, so a theme travels between them.

Every theme is checked for readability: the palettes come from other people, and
their dim colours were chosen for their own background rather than for panels
and hover rows. Rather than editing someone else's palette, the stylesheet
nudges a colour only as far as it takes to clear the WCAG bar, and leaves
anything that already passes exactly as its author drew it. A test enforces it.

## Install

```sh
git clone <this repo> rose-bouquet
cd rose-bouquet
python -m venv .venv && . .venv/bin/activate
pip install -e '.[online]'
rose-bouquet
```

Python 3.12+, PySide6, mutagen. `yt-dlp` and `ytmusicapi` add the YouTube half;
without them it is a local music player that says so. `cava` drives the
visualiser; without it the visualiser stays flat and nothing else changes.

`packaging/install-desktop-entry.sh` adds it to your application menu.

## Keys

| Key | Does |
|---|---|
| `Space` | Play / pause |
| `Ctrl+→` / `Ctrl+←` | Next / previous |
| `Ctrl+H` | Shuffle |
| `Ctrl+R` | Repeat |
| `Ctrl+Q` | Show the queue |
| `Ctrl+F` | Search whatever section you are in |
| `Ctrl+1`…`9` | Jump to a section |
| `Ctrl+,` | Settings |

```sh
rose-bouquet --section watch      # open on a section
rose-bouquet --serve              # start the server on launch
rose-bouquet --theme matrix       # try a theme without changing the setting
```

## Where things live

```
~/.local/share/rose-bouquet/
  library.json     what was found on disk, a cache you can delete
  tastes.json      subscriptions, likes and history — the whole profile
  imports/         one record per playlist import, so they can resume
  playlists/       M3U files
  downloads/       only used if you have no music folder set
~/.config/rose-bouquet/
  preferences.json theme, style, folders, server settings
  credentials.json Spotify keys and the server password, owner-only
```

Downloads land in your music folder by default, so they join the library on the
next scan.

## Two things worth knowing

**YouTube blocks anonymous downloads** with "Sign in to confirm you're not a
bot" on its default client. Rose Bouquet asks the TV and mobile clients first,
which still answer. If that stops working, Settings → Downloads can read cookies
from a local Firefox or Waterfox profile — off by default, because an app should
not touch your cookie jar unasked.

**Spotify caps unauthenticated playlist reads at 100 tracks.** Their public
token endpoints are now signed and refuse anonymous requests, so a longer
playlist needs either your own free API credentials (Settings → Downloads, and
imports then page properly) or an Exportify CSV pasted into the import box.
When an import is capped, it says so rather than pretending the playlist was
short.

Downloading from YouTube is against YouTube's terms of service, whatever the
local position on personal-use copies. That is the same trade `yt-dlp` carries,
and it is yours to make.

## Tests

```sh
pip install -e '.[dev]'
pytest
```

99 tests over the queue, the library, playlists, the cava bridge, the Spotify
importer and its paging, resumable imports, the recommender, the server, and
theme readability.

## Android

`android/` holds a Kotlin/Compose client that speaks the same Subsonic API —
the client, the Media3 playback service, the settings store and the Rose
palettes are written; the Compose screens are not finished. It needs JDK 21
(`sudo pacman -S jdk21-openjdk`) since Android Gradle cannot use JDK 26.

## Credits

The rose is **"rose (3/99)" by Joan G. Stark ("jgs")**, from her
[archived gallery](https://github.com/oldcompcz/jgs). The same rose marks every
Rose project.

Feature design owes a debt to [Pear Desktop](https://github.com/pear-devs/pear-desktop)
for the YouTube Music side and to Navidrome for the server side.

## Licence

GPL-3.0-or-later. A project of **Rose Open Source Endeavours**.
