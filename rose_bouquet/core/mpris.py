"""MPRIS2: what the desktop's media controls talk to.

Every Linux media widget — a desktop bar, `playerctl`, the headset's play
button routed through it — speaks one protocol, and it is this one: a D-Bus
service named `org.mpris.MediaPlayer2.<something>` exporting a transport at
`/org/mpris/MediaPlayer2`. Register that and Rose Bouquet stops being a window
you have to focus and starts being *the media player*, the same as any other.

**QtDBus rather than a D-Bus library.** PySide6 already ships `QtDBus`, and it
already runs on Qt's event loop, so calls arrive on the interface thread and can
touch the player directly. A separate binding would mean a new dependency and a
second event loop to marshal across for no gain.

The spec's shape is worth knowing before reading further: two interfaces on one
object. `org.mpris.MediaPlayer2` is the application (raise it, quit it), and
`org.mpris.MediaPlayer2.Player` is the transport (play, pause, metadata,
volume). Controllers read state through the standard properties interface, so
every change has to be announced with `PropertiesChanged` — a player that
updates its own state silently looks frozen in every bar on the system.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import (
    ClassInfo,
    Property,
    QObject,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtDBus import (
    QDBusAbstractAdaptor,
    QDBusConnection,
    QDBusMessage,
    QDBusObjectPath,
)

from rose_bouquet.core import artwork
from rose_bouquet.core.playqueue import Repeat

logger = logging.getLogger(__name__)

OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"

#: Bus names must start with this to be found. The suffix is ours.
BUS_PREFIX = "org.mpris.MediaPlayer2."
BUS_NAME = BUS_PREFIX + "rose_bouquet"

#: A "no track" placeholder the spec defines; controllers know to ignore it.
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

_LOOP_TO_REPEAT = {"None": Repeat.OFF, "Playlist": Repeat.ALL, "Track": Repeat.ONE}
_REPEAT_TO_LOOP = {v: k for k, v in _LOOP_TO_REPEAT.items()}


@ClassInfo({"D-Bus Interface": ROOT_INTERFACE})
class _RootAdaptor(QDBusAbstractAdaptor):
    """The application half: identity, and the window behind it."""

    def __init__(self, service: "Mpris") -> None:
        super().__init__(service)
        self._service = service

    @Slot()
    def Raise(self) -> None:  # noqa: N802 - D-Bus method name
        self._service.raise_window()

    @Slot()
    def Quit(self) -> None:  # noqa: N802
        self._service.quit()

    @Property(bool)
    def CanQuit(self) -> bool:  # noqa: N802
        return True

    @Property(bool)
    def CanRaise(self) -> bool:  # noqa: N802
        return self._service.window is not None

    @Property(bool)
    def HasTrackList(self) -> bool:  # noqa: N802
        # The queue is not exported as an MPRIS TrackList; saying so honestly
        # stops controllers asking for an interface that is not there.
        return False

    @Property(str)
    def Identity(self) -> str:  # noqa: N802
        return "Rose Bouquet"

    @Property(str)
    def DesktopEntry(self) -> str:  # noqa: N802
        """The .desktop file, minus the suffix — this is where the icon in the
        bar comes from, so it has to match what the installer wrote."""
        return "rose-bouquet"

    @Property("QStringList")
    def SupportedUriSchemes(self) -> list:  # noqa: N802
        return ["file"]

    @Property("QStringList")
    def SupportedMimeTypes(self) -> list:  # noqa: N802
        return [
            "audio/mpeg", "audio/flac", "audio/ogg", "audio/x-vorbis+ogg",
            "audio/mp4", "audio/x-m4a", "audio/wav", "audio/x-wav",
            "audio/opus", "audio/aac",
        ]

    @Property(bool)
    def Fullscreen(self) -> bool:  # noqa: N802
        return self._service.fullscreen

    @Fullscreen.setter
    def Fullscreen(self, value: bool) -> None:  # noqa: N802
        self._service.set_fullscreen(bool(value))

    @Property(bool)
    def CanSetFullscreen(self) -> bool:  # noqa: N802
        return self._service.window is not None


@ClassInfo({"D-Bus Interface": PLAYER_INTERFACE})
class _PlayerAdaptor(QDBusAbstractAdaptor):
    """The transport half: everything a media widget actually presses."""

    Seeked = Signal("qlonglong")

    def __init__(self, service: "Mpris") -> None:
        super().__init__(service)
        self._service = service

    # ── Methods ───────────────────────────────────────────────────

    @Slot()
    def Next(self) -> None:  # noqa: N802
        self._service.playback.next()

    @Slot()
    def Previous(self) -> None:  # noqa: N802
        self._service.playback.previous()

    @Slot()
    def Pause(self) -> None:  # noqa: N802
        self._service.playback.pause()

    @Slot()
    def PlayPause(self) -> None:  # noqa: N802
        self._service.playback.toggle()

    @Slot()
    def Stop(self) -> None:  # noqa: N802
        self._service.playback.stop()

    @Slot()
    def Play(self) -> None:  # noqa: N802
        self._service.playback.play()

    @Slot("qlonglong")
    def Seek(self, offset: int) -> None:  # noqa: N802
        """Seek by a relative amount, in microseconds."""
        playback = self._service.playback
        target = playback.position + int(offset) // 1000
        duration = playback.duration
        # Seeking past the end means "next track", not "clamp to the end" —
        # a bar's scrub-to-the-right should behave like the skip button.
        if duration and target >= duration:
            playback.next()
            return
        playback.seek(max(0, target))

    @Slot(QDBusObjectPath, "qlonglong")
    def SetPosition(self, track_id: QDBusObjectPath, position: int) -> None:  # noqa: N802
        """Seek to an absolute position, in microseconds.

        The track id guards against a stale request: a controller that decided
        to seek just as the track changed must not scrub the new one.
        """
        if track_id.path() != self._service.track_id:
            return
        self._service.playback.seek(max(0, int(position) // 1000))

    @Slot(str)
    def OpenUri(self, uri: str) -> None:  # noqa: N802
        self._service.open_uri(uri)

    # ── Properties ────────────────────────────────────────────────

    @Property(str)
    def PlaybackStatus(self) -> str:  # noqa: N802
        return self._service.playback_status

    @Property(str)
    def LoopStatus(self) -> str:  # noqa: N802
        return _REPEAT_TO_LOOP.get(self._service.playback.queue.repeat, "None")

    @LoopStatus.setter
    def LoopStatus(self, value: str) -> None:  # noqa: N802
        mode = _LOOP_TO_REPEAT.get(value)
        if mode is not None:
            self._service.playback.set_repeat(mode)

    @Property(bool)
    def Shuffle(self) -> bool:  # noqa: N802
        return self._service.playback.queue.shuffle

    @Shuffle.setter
    def Shuffle(self, value: bool) -> None:  # noqa: N802
        self._service.playback.set_shuffle(bool(value))

    @Property("QVariantMap")
    def Metadata(self) -> dict:  # noqa: N802
        return self._service.metadata()

    @Property(float)
    def Volume(self) -> float:  # noqa: N802
        return float(self._service.playback.volume)

    @Volume.setter
    def Volume(self, value: float) -> None:  # noqa: N802
        self._service.playback.set_volume(float(value))

    @Property("qlonglong")
    def Position(self) -> int:  # noqa: N802
        """Microseconds. Deliberately not announced through PropertiesChanged —
        the spec has controllers extrapolate between `Seeked` signals rather
        than have every player wake the bus a few times a second."""
        return int(self._service.playback.position) * 1000

    @Property(float)
    def Rate(self) -> float:  # noqa: N802
        return 1.0

    @Rate.setter
    def Rate(self, value: float) -> None:  # noqa: N802
        # Fixed at 1.0; the spec requires the property to be writable anyway.
        return

    @Property(float)
    def MinimumRate(self) -> float:  # noqa: N802
        return 1.0

    @Property(float)
    def MaximumRate(self) -> float:  # noqa: N802
        return 1.0

    @Property(bool)
    def CanGoNext(self) -> bool:  # noqa: N802
        return len(self._service.playback.queue) > 0

    @Property(bool)
    def CanGoPrevious(self) -> bool:  # noqa: N802
        return len(self._service.playback.queue) > 0

    @Property(bool)
    def CanPlay(self) -> bool:  # noqa: N802
        return self._service.playback.track is not None

    @Property(bool)
    def CanPause(self) -> bool:  # noqa: N802
        return self._service.playback.track is not None

    @Property(bool)
    def CanSeek(self) -> bool:  # noqa: N802
        return self._service.playback.duration > 0

    @Property(bool)
    def CanControl(self) -> bool:  # noqa: N802
        return True


class Mpris(QObject):
    """Rose Bouquet on the session bus.

    Built through `start()`, which returns None when there is no session bus to
    join — a headless test run, a broken `DBUS_SESSION_BUS_ADDRESS`. Media
    controls are a nicety; not having them is never a reason to fail to launch.
    """

    def __init__(self, playback, window=None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.playback = playback
        self.window = window

        self._root = _RootAdaptor(self)
        self._player = _PlayerAdaptor(self)

        #: Object path of the current track. Controllers use it to tell one
        #: track from the next, so it has to change on every load — including
        #: a repeat of the same file.
        self.track_id = NO_TRACK
        self._track_serial = 0
        self._last_status = ""

        #: Worked out once per track rather than per read: `Metadata` is polled
        #: by every controller on the bus, and resolving art can mean parsing
        #: the file's tags.
        self._art_url = ""

        playback.track_changed.connect(self._on_track_changed)
        playback.state_changed.connect(self._on_state_changed)
        playback.queue_changed.connect(self._on_queue_changed)
        playback.volume_changed.connect(self._on_volume_changed)
        playback.seeked.connect(self._on_seeked)

    # ── Registration ──────────────────────────────────────────────

    @classmethod
    def start(cls, playback, window=None, parent: Optional[QObject] = None) -> Optional["Mpris"]:
        connection = QDBusConnection.sessionBus()
        if not connection.isConnected():
            logger.info("no session bus; media keys and desktop controls unavailable")
            return None

        service = cls(playback, window, parent)

        # Adaptors only. `ExportAllContents` would put this object's own
        # methods on the bus as a made-up interface — every private slot
        # visible and callable by anything on the session.
        if not connection.registerObject(
            OBJECT_PATH, service,
            QDBusConnection.RegisterOption.ExportAdaptors,
        ):
            logger.warning("could not export %s on the session bus", OBJECT_PATH)
            service.deleteLater()
            return None

        # A second copy of the player is legal and the spec says how: append a
        # unique suffix rather than fighting the first one for the plain name.
        name = BUS_NAME
        if not connection.registerService(name):
            import os

            name = f"{BUS_NAME}.instance{os.getpid()}"
            if not connection.registerService(name):
                logger.warning("could not claim an MPRIS bus name")
                connection.unregisterObject(OBJECT_PATH)
                service.deleteLater()
                return None

        service._bus_name = name
        logger.info("registered on the session bus as %s", name)

        # A restored session has already loaded its track by the time this
        # runs, and that load's signal went nowhere. Without this the bar shows
        # the right title but no artwork and a track id of "NoTrack", so
        # seeking from the bar is refused until the next track starts.
        if playback.track is not None:
            service._on_track_changed(playback.track)

        return service

    def stop(self) -> None:
        connection = QDBusConnection.sessionBus()
        name = getattr(self, "_bus_name", "")
        if name:
            connection.unregisterService(name)
        connection.unregisterObject(OBJECT_PATH)

    # ── State the adaptors read ───────────────────────────────────

    @property
    def playback_status(self) -> str:
        if self.playback.playing:
            return "Playing"
        return "Paused" if self.playback.track is not None else "Stopped"

    @property
    def fullscreen(self) -> bool:
        return bool(self.window is not None and self.window.isFullScreen())

    def set_fullscreen(self, value: bool) -> None:
        if self.window is None:
            return
        self.window.showFullScreen() if value else self.window.showNormal()

    def metadata(self) -> dict:
        """The current track as MPRIS describes tracks.

        Times are microseconds and the artist is a list, both because the spec
        says so; getting either wrong shows up as a bar with no duration or an
        artist line reading `['Name']`.
        """
        track = self.playback.track
        if track is None:
            return {"mpris:trackid": QDBusObjectPath(NO_TRACK)}

        data = {
            "mpris:trackid": QDBusObjectPath(self.track_id),
            "mpris:length": int(self.playback.duration) * 1000,
            "xesam:title": track.display_title,
            "xesam:artist": [track.display_artist],
            "xesam:album": track.display_album,
            "xesam:url": QUrl.fromLocalFile(track.path).toString(),
        }

        if track.album_artist:
            data["xesam:albumArtist"] = [track.album_artist]
        if track.track_number:
            data["xesam:trackNumber"] = int(track.track_number)
        if track.disc_number:
            data["xesam:discNumber"] = int(track.disc_number)
        if track.genre:
            data["xesam:genre"] = [track.genre]
        art = self._art_url
        if art:
            data["mpris:artUrl"] = art

        return data

    # ── Acting on what a controller asked for ─────────────────────

    def raise_window(self) -> None:
        if self.window is None:
            return
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self) -> None:
        if self.window is not None:
            self.window.close()

    def open_uri(self, uri: str) -> None:
        """Play a file handed over by another program."""
        url = QUrl(uri)
        path = url.toLocalFile() if url.isLocalFile() else uri
        track = self.playback.library.track(path)
        if track is not None:
            self.playback.play_tracks([track], 0)

    # ── Announcing changes ────────────────────────────────────────

    def _changed(self, interface: str, properties: dict) -> None:
        message = QDBusMessage.createSignal(
            OBJECT_PATH, PROPERTIES_INTERFACE, "PropertiesChanged",
        )
        message.setArguments([interface, properties, []])
        QDBusConnection.sessionBus().send(message)

    def _on_track_changed(self, track) -> None:
        self._art_url = artwork.art_url(track)
        self._track_serial += 1
        self.track_id = f"/org/mpris/MediaPlayer2/rose_bouquet/track/{self._track_serial}"
        self._changed(PLAYER_INTERFACE, {
            "Metadata": self.metadata(),
            "PlaybackStatus": self.playback_status,
            "CanPlay": self.playback.track is not None,
            "CanPause": self.playback.track is not None,
            "CanSeek": self.playback.duration > 0,
        })

    def _on_state_changed(self, _playing: bool) -> None:
        status = self.playback_status
        if status == self._last_status:
            return
        self._last_status = status
        self._changed(PLAYER_INTERFACE, {"PlaybackStatus": status})

    def _on_queue_changed(self) -> None:
        queue = self.playback.queue
        self._changed(PLAYER_INTERFACE, {
            "Shuffle": queue.shuffle,
            "LoopStatus": _REPEAT_TO_LOOP.get(queue.repeat, "None"),
            "CanGoNext": len(queue) > 0,
            "CanGoPrevious": len(queue) > 0,
        })

    def _on_volume_changed(self, volume: float) -> None:
        self._changed(PLAYER_INTERFACE, {"Volume": float(volume)})

    def _on_seeked(self, position: int) -> None:
        self._player.Seeked.emit(int(position) * 1000)
