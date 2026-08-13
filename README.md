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

> **Status: alpha.** The core works and is tested; some of it has had very
> little real-world use. Nothing below is a placeholder pretending to be
> finished — where something only half works, it says so.

## ⚠️ Vibecoders begone

Yes, this is a vibecoded project. I'm new to programming and still learning
Python - I couldn't have written all of this myself yet, so I leaned on my Local LLM
for assistance to get a working prototype off the ground. This is a starting
point, not a finished, "proper" codebase.

**From here on, I want contributions to be human-written.** If you have
ideas, feature requests, or bug reports, please open an
[Issue](https://github.com/Solidifiedinstone/Rose-Bouquet/issues) instead of
sending an AI-generated PR. My goal is to actually learn and own this
codebase as I keep improving at Python, and eventually replace all of it
with code written by hand - mine or a human contributor's.

## Expect bugs

This is early software and it will break on things I have not seen. It is
developed on Arch-based Linux with PipeWire, a local music folder and an
imported YouTube history - your distribution, your audio stack, your library
and your disc drive are all different, and that is exactly where it will fall
over.

**Please post anything that goes wrong in the
[Issues tab](https://github.com/Solidifiedinstone/Rose-Bouquet/issues).** Run it
from a terminal and paste what it printed, and say which distribution you are
on. There is also a log at `~/.local/share/rose-bouquet/logs/rose-bouquet.log`
which is usually more use than the terminal.

## What it does

**Watch** — a YouTube feed built on this machine, from what you follow and what
you have actually watched, plus search and full video playback with a real
transport. Every item says *why* it is there: "You follow Warp Records", "You
liked something by Boards of Canada", "Because you watch Techmoan". Nothing you
have already watched comes back, one video never floods the feed with its whole
channel, and a slice is reserved for channels you have no history with, so it
cannot collapse into the same five names. Switch any video to audio-only and
keep your position; download it, follow the channel, or like it from the
player.

**Shorts** — a vertical reel you scroll with the wheel, arrow keys or a swipe,
built from the same profile but kept *separate* from your video history, so
doomscrolling for ten minutes does not rewrite what the Watch tab thinks you
like. Videos unload as they leave the screen and the next page loads before you
reach it.

**Your interests, configurable and actually obeyed** — say what you want more
of and what you never want to see, by topic or by channel, and it is enforced
as a filter rather than a hint. On by default is a filter for engagement-bait
and AI-generated slop, which is the thing an algorithm optimised for watch time
will otherwise feed you.

**Following** — subscriptions kept locally rather than on an account. Paste a
channel link, or follow anything you are watching. Mute a channel to keep
following it without seeing it in the feed.

**Browse** — new music and new channels near what you already listen to, for
finding things your subscriptions would never have shown you.

**Library** — your own files, scanned from folders you choose, with tags read
by mutagen. Albums group properly, including compilations. Shuffle is a
shuffled *order* rather than a random pick, so nothing repeats before the album
is through and "previous" means something.

**Playlists** — extended M3U files in a folder, readable by every other player
ever written.

**Import from Spotify, or from Google Takeout** — Spotify imports bring the
track list across and find each song on YouTube Music; the ones it *cannot*
find are listed and saved with the playlist rather than quietly dropped. A
Takeout archive brings your real YouTube watch history in, which is what makes
the feed yours on the first run rather than the hundredth. An import is a
record on disk, so an interrupted one carries on where it stopped and nothing
downloads twice.

**Disc** — play an audio CD straight from the drive (streamed, not ripped
first), rip one to your library with proper track tags, burn a playlist back to
a blank, and read data or video discs. It names any missing tool and the
command to install it rather than failing quietly.

**Serve** — hosts your library over the local network using the Subsonic API,
so the phone in your pocket can play it, and so can Symfonium, DSub,
substreamer or Feishin. Byte-range streaming, so seeking works.

**Plays like part of the desktop** — MPRIS, so your bar, your lock screen and
your media keys control it and show the album art, scraped from the file's own
tags or from YouTube.

**The visualiser** — cava-driven, with 21 shapes (waves, bars, mirrored,
radial, a turntable that uses the album art as the record label, and more), a
reactivity slider, a frame-rate cap for machines that cannot hold 60, per-shape
scale, colour modes from solid through rainbow with fade, sweep, flash and
pulse motions — and shapes can be layered over each other to build your own.
There is a fullscreen mode with its own transport.

## Appearance

25 themes and 11 styles, and any theme composes with any style — the same
palettes as Rose GameLab and Rose Productivity, so a theme travels between them.

Every theme is checked for readability: the palettes come from other people, and
their dim colours were chosen for their own background rather than for panels
and hover rows. Rather than editing someone else's palette, the stylesheet
nudges a colour only as far as it takes to clear the WCAG bar, and leaves
anything that already passes exactly as its author drew it. A test enforces it.

## Installing on Linux

Rose Bouquet is not packaged yet — no Flatpak, no AUR package. Until then it
installs with **pipx** in about a minute, and cleanly: pipx gives the
application its own isolated environment, puts one command on your `PATH`, and
writes nothing outside your home directory.

Use pipx rather than `pip` because most distributions now ship a Python that
refuses `pip install` outright (PEP 668, "externally managed environment").
pipx works on those distributions without arguments, and uninstalls just as
cleanly.

### What you need

- **Linux** with a Wayland or X11 session
- **Python 3.12 or newer** — `python --version` to check
- **pipx** — `sudo pacman -S python-pipx`, `sudo apt install pipx`,
  `sudo dnf install pipx`, or `sudo zypper install python3-pipx`
- **Qt 6 system libraries**, which your distribution already ships

Most distributions have everything already. If PySide6 fails to start with a
missing-library error, install Qt's runtime dependencies:

```sh
# Arch, Artix, Manjaro, EndeavourOS
sudo pacman -S --needed python qt6-base qt6-multimedia

# Debian, Ubuntu, Mint, Pop!_OS
sudo apt install python3 python3-venv libgl1 libxkbcommon-x11-0 libegl1

# Fedora
sudo dnf install python3 qt6-qtbase qt6-qtmultimedia

# openSUSE
sudo zypper install python312 libQt6Gui6 libQt6Multimedia6
```

### Install

```sh
pipx install "git+https://github.com/Solidifiedinstone/Rose-Bouquet#egg=rose-bouquet[online]"
```

That is the whole install. To run it:

```sh
rose-bouquet
```

If your shell cannot find the command, `pipx ensurepath` adds pipx's directory
to your `PATH` — then open a new terminal.

The `[online]` part pulls in `yt-dlp`, `ytmusicapi` and `requests`, which are
what the YouTube, Shorts and Browse tabs need. Leave it off and you get a local
music player that says plainly which parts are unavailable:

```sh
pipx install git+https://github.com/Solidifiedinstone/Rose-Bouquet
```

Prefer to keep a copy of the source (you want the desktop entry below, or you
plan to poke at the code)? Clone first and install from the folder:

```sh
git clone https://github.com/Solidifiedinstone/Rose-Bouquet
cd Rose-Bouquet
pipx install '.[online]'
```

### Optional extras

Neither is required, and both are checked for at runtime rather than assumed:

- **cava** drives the visualiser. Without it the visualiser stays flat and
  nothing else changes. `sudo pacman -S cava` / `sudo apt install cava`.
- **cdparanoia** and **cdrskin** drive the Disc tab — playing, ripping and
  burning CDs. The tab names whichever is missing and the command to install
  it. `sudo pacman -S cdparanoia libburn`.

### Put it in your application menu

From a clone of the repository:

```sh
./packaging/install-desktop-entry.sh
```

This adds a desktop entry and icons under `~/.local`, so Rose Bouquet appears
in your launcher and dock like any other application. It needs no root, and
`./packaging/install-desktop-entry.sh --uninstall` removes it again.

It writes the launcher's **absolute path** into the entry on purpose: desktop
files do not inherit your shell's `PATH`, so a bare command name works in a
terminal and then silently fails from a dock.

### Updating and removing

```sh
pipx upgrade rose-bouquet
pipx uninstall rose-bouquet
```

Uninstalling leaves your library, playlists and taste profile alone — they live
under `~/.local/share/rose-bouquet` and `~/.config/rose-bouquet`, and deleting
those two folders removes every trace.

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

In the Shorts reel, the wheel and `↑`/`↓` move between shorts and `Esc` leaves
the reel. The visualiser has a fullscreen button beside it, and `Esc` comes
back.

```sh
rose-bouquet --section feed       # open on a section
rose-bouquet --serve              # start the server on launch
rose-bouquet --theme matrix       # try a theme without changing the setting
```

## Where things live

```
~/.local/share/rose-bouquet/
  library.json     what was found on disk, a cache you can delete
  tastes.json      subscriptions, likes, interests and history — the profile
  feed.json        the last feed that was built, so a cold start is instant
  session.json     what was playing and where, restored on the next launch
  covers/          album art, cached
  logs/            a rolling log, the first thing to look at when something breaks
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
bot" on its default client. Rose Bouquet asks the Android client first, which
still answers with URLs that actually play — the TV and web clients hand back
links that 403 on fetch. If that stops working, Settings → Downloads can read cookies
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

## Development

Working on Rose Bouquet rather than just running it? Use a virtualenv with an
editable install — pipx is for installing the application, not developing it.

```sh
git clone https://github.com/Solidifiedinstone/Rose-Bouquet
cd Rose-Bouquet
python -m venv .venv
.venv/bin/pip install -e '.[online,dev]'
.venv/bin/python -m pytest -q
```

221 tests over the queue, the library, playlists, the cava bridge, the Spotify
and Takeout importers, resumable imports, the recommender and its interest
filtering, the shorts reel, optical discs, the server, and theme readability —
including one that launches the real entry point and waits for the window,
because a deadlock on startup is invisible to every test that builds the
window by hand.

None of them touch the network or need an optical drive.

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
