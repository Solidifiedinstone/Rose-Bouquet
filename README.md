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

# Bouquet

**Rose Bouquet for Android.** Your own music server in your pocket, and
YouTube without the algorithm.

A native Android client for [Rose Bouquet](https://github.com/Solidifiedinstone/Rose-Bouquet)
— and for Navidrome, Airsonic, Gonic or anything else that speaks Subsonic.
Stream your library over the network, download what you want offline, and watch
YouTube through a feed built on your phone from what you actually watch.

</div>

---

> **Status: alpha.** It builds, installs and runs on a phone, and the logic that
> decides what you see is covered by tests. What it has not had is months of
> real use across many devices. Expect rough edges and please report them.

## Expect bugs

This is early software. It has been built and tested on a desktop against the
Subsonic protocol, but Android is a thousand different devices and yours is
probably not the one it was written on.

**Please post anything that goes wrong in the
[Issues tab](https://github.com/Solidifiedinstone/Rose-Bouquet-Android/issues)**,
with your Android version and which server you are connecting to.

## What it does

**Connects to servers, not just one server.** Add a home library, a friend's,
and a VPS, and switch between them with a tap. The phone is the thing that
moves — re-typing an address and password to change which library you are
listening to is the kind of friction that means you stop bothering.

**Stream it, or download it.** Streaming is the default and downloading is one
tap on any album or track. Downloads never expire and are never evicted to make
room for something you streamed once; the two live in separate caches for
exactly that reason. Downloaded music plays with the server switched off and
the phone in aeroplane mode.

**Plays like a real music app.** Lock-screen and notification controls,
Bluetooth and headset buttons, audio focus that ducks for navigation and pauses
for a call, and playback that survives the app being backgrounded.

**Watch, without the engagement machine.** A YouTube feed built on this phone
from the channels you actually watch — not only the ones you subscribed to.
Every row says *why* it is there: "You follow Techmoan", "Because you watch
about tape decks". Nothing you have already watched comes back, and no single
channel is allowed more than a fifth of the feed.

**Shorts, kept separate.** A vertical reel you swipe through, built from its
own history. Ten minutes of doomscrolling does not rewrite what the Watch tab
thinks you like — the two histories never mix, which is deliberate and is the
whole reason the feature is safe to have.

**An algorithm you can actually configure.** Say what you want more of and what
you never want to see, by topic or by channel. Blocking removes; it does not
"reduce". There is also a filter for engagement bait and AI slop, on by
default, which matches on word boundaries so it catches `(AI COVER)` without
eating a Sorabji piano recital.

**The visualiser**, with the desktop's 21 shapes — waves, bars, mirrored, the
radial family, the turntable — layerable over each other, with the four colour
modes, five motions and a reactivity slider. Driven by Android's own audio
spectrum rather than cava, from the same shape of data, so the shapes look the
same.

**Import your history and your playlists.** A recommender with no history has
nothing to work from, so Import takes a Google Takeout `.zip` straight from
Google — no unpacking needed, both the JSON and HTML formats. Spotify playlists
and Exportify CSVs are matched against your own library first and fetched from
YouTube Music only where you do not already own the track, and whatever could
not be found is listed rather than quietly dropped.

**Sensible about mobile data.** Stream on wifi only, and cap the quality the
server sends when you are not on wifi — both off by default, and neither
touches what you get on wifi.

**All 25 Rose themes and 11 styles**, generated from the desktop app's own
palette data so a theme looks the same on the phone as on the desktop.

**No account, no telemetry, no API key.** Your subscriptions, history and taste
profile are rows in a database on your phone. Uninstalling deletes them.

## Installing

There is no Play Store listing and no F-Droid entry yet, so this installs as an
APK.

1. Download `bouquet.apk` from the
   [latest release](https://github.com/Solidifiedinstone/Rose-Bouquet-Android/releases/latest).
2. Open it. Android will ask whether to allow installing apps from this source —
   that prompt is normal for anything not from the Play Store, and it is asking
   about your browser or file manager, not about this app.
3. Open Bouquet, tap the menu, go to **Settings → Add a server**, and enter the address,
   username and password of your Subsonic server. The address includes the
   port, for example `http://192.168.1.10:4533`.
4. Press **Rescan library**. The first scan reads every album, so a large
   library takes a minute.

Needs **Android 8.0 (Oreo) or newer**.

The release APK is unsigned by any store and signed with a debug key, so
Android will show it as coming from an unknown developer. That is accurate.
Building it yourself is the way to avoid trusting a stranger's binary, and the
next section is how.

### Building it yourself

```sh
git clone https://github.com/Solidifiedinstone/Rose-Bouquet-Android
cd Rose-Bouquet-Android
./gradlew assembleDebug
```

The APK lands in `app/build/outputs/apk/debug/`.

You need **JDK 21** (not 26 — the Android Gradle plugin does not accept it yet)
and the **Android SDK** with platform 35. If you have Android Studio, both come
with it and it will offer to install anything missing. Without it:

```sh
# Arch, Artix, Manjaro
sudo pacman -S jdk21-openjdk

export JAVA_HOME=/usr/lib/jvm/java-21-openjdk
export ANDROID_HOME=$HOME/Android/Sdk
```

Run the tests with `./gradlew testDebugUnitTest`.

## Using it with the desktop app

Turn on **Serve** in Rose Bouquet on the desktop, note the address and password
it shows, and add that as a server here. Everything the desktop app has scanned
is then playable on the phone.

It speaks plain Subsonic, so it works just as well against Navidrome, Airsonic,
Gonic or Ampache — and the desktop app's server works with Symfonium, DSub,
substreamer and Feishin. Two implementations of an open protocol beats two ends
of a private one.

Servers differ in what they implement, and that is normal. A method a server
does not support greys out one screen rather than breaking the app: the desktop
app's own server has no playlist endpoints, for instance, so the Playlists tab
is empty against it and full against Navidrome.

## Where things live

```
Room database    library cache, downloads, watch history, subscriptions, feed
DataStore        servers and credentials, theme, interests
files/downloads  downloaded audio — never evicted
cache/stream     recently streamed audio — capped at 512 MB, evicts oldest
```

Uninstalling removes all of it.

**On credentials:** Subsonic's authentication requires the client to hold the
real password — the salted token it sends is computed from it per request, so
there is nothing weaker to store instead. It lives in this app's private
storage, which is as far as the protocol allows.

## What is not done

Stated plainly rather than left to be discovered:

- **YouTube will break this periodically.** It is scraped, not an API. When
  YouTube changes a page, extraction fails until NewPipeExtractor is updated.
  This is inherent to the approach rather than a bug to be fixed.
- **The visualiser needs `RECORD_AUDIO`,** because Android classes reading an
  app's own output as recording and offers nothing narrower. It is asked for
  only when you open the visualiser, and refusing costs the visualiser alone.
  Some devices and some Bluetooth routes refuse to attach it at all; the screen
  says so rather than sitting there flat with no explanation.
- **Release builds are shrunk,** with keep rules for NewPipeExtractor, Rhino
  and jsoup — all three resolve classes by name. Check the Watch tab after any
  dependency bump: shrinking is the change that fails only on YouTube.

## Credits

The rose is **"rose (3/99)" by Joan G. Stark ("jgs")**, from her
[archived gallery](https://github.com/oldcompcz/jgs). The same rose marks every
Rose project.

The YouTube half is [NewPipeExtractor](https://github.com/TeamNewPipe/NewPipeExtractor),
without which none of it would be possible.

## Licence

GPL-3.0-or-later. A project of **R.O.S.E. — Rose Open Source Endeavours**.
