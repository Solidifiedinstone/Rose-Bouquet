package dev.rose.bouquet.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.util.UnstableApi
import android.net.Uri
import dev.rose.bouquet.data.Imports
import dev.rose.bouquet.data.MusicRepository
import dev.rose.bouquet.data.Network
import dev.rose.bouquet.data.Server
import dev.rose.bouquet.data.ServerStore
import dev.rose.bouquet.data.Settings
import dev.rose.bouquet.data.SettingsStore
import dev.rose.bouquet.data.SubsonicClient
import dev.rose.bouquet.data.toEntity
import dev.rose.bouquet.data.db.AlbumEntity
import dev.rose.bouquet.data.db.ChannelEntity
import dev.rose.bouquet.data.db.FeedEntity
import dev.rose.bouquet.data.db.OpinionEntity
import dev.rose.bouquet.data.db.RoseDatabase
import dev.rose.bouquet.data.db.SongEntity
import dev.rose.bouquet.data.db.WatchEntity
import dev.rose.bouquet.player.DownloadStore
import dev.rose.bouquet.player.PlaybackState
import dev.rose.bouquet.player.PlayerConnection
import dev.rose.bouquet.ui.screens.Layer
import dev.rose.bouquet.youtube.Interests
import dev.rose.bouquet.youtube.deriveTopics
import dev.rose.bouquet.youtube.keep
import dev.rose.bouquet.youtube.Recommender
import dev.rose.bouquet.youtube.Video
import dev.rose.bouquet.youtube.YouTubeSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

/**
 * One state holder for the whole app.
 *
 * A single view model rather than one per screen, because nearly everything
 * here is shared: the active server decides what the library, search and
 * downloads all show, and the player is the same player on every screen.
 * Splitting it would mostly produce machinery for keeping the pieces in sync.
 */
@UnstableApi
class AppViewModel(app: Application) : AndroidViewModel(app) {

    private val database = RoseDatabase.get(app)
    private val servers = ServerStore(app)
    private val settingsStore = SettingsStore(app)
    private val repository = MusicRepository(app)
    private val client = SubsonicClient()
    private val recommender = Recommender(database)
    private val youtubeDao = database.youtube()

    val player = PlayerConnection(app)
    val playback: StateFlow<PlaybackState> get() = player.state

    // ── Settings and servers ──────────────────────────────────────

    val settings: StateFlow<Settings> = settingsStore.settings
        .stateIn(viewModelScope, SharingStarted.Eagerly, Settings())

    val serverList: StateFlow<List<Server>> = servers.servers
        .stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    val activeServer: StateFlow<Server?> = servers.active
        .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    // ── The library ───────────────────────────────────────────────

    @OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
    val songs: StateFlow<List<SongEntity>> = activeServer
        .flatMapLatest { repository.songs(it) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    @OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
    val albums: StateFlow<List<AlbumEntity>> = activeServer
        .flatMapLatest { repository.albums(it) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    @OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
    val downloads: StateFlow<List<SongEntity>> = activeServer
        .flatMapLatest { repository.downloaded(it) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing.asStateFlow()

    private val _status = MutableStateFlow<String?>(null)
    val status: StateFlow<String?> = _status.asStateFlow()

    // ── YouTube ───────────────────────────────────────────────────

    val watchFeed: StateFlow<List<FeedEntity>> = youtubeDao.feed(shorts = false)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    val shortsFeed: StateFlow<List<FeedEntity>> = youtubeDao.feed(shorts = true)
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    val channels: StateFlow<List<ChannelEntity>> = youtubeDao.channels()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    private val _buildingFeed = MutableStateFlow(false)
    val buildingFeed: StateFlow<Boolean> = _buildingFeed.asStateFlow()

    init {
        player.connect()
        // The extractor reads a couple of pages on first use; getting that out
        // of the way at launch means the first tap on Watch is not the one that
        // pays for it.
        viewModelScope.launch(Dispatchers.IO) { YouTubeSource.start() }
    }

    override fun onCleared() {
        player.release()
        super.onCleared()
    }

    // ── Servers ───────────────────────────────────────────────────

    /**
     * Check a server before saving it.
     *
     * Saving first and discovering later that the password is wrong leaves
     * somebody staring at an empty library with nothing saying why.
     */
    suspend fun testServer(url: String, username: String, password: String): Result<Unit> =
        withContext(Dispatchers.IO) {
            runCatching {
                client.ping(Server("test", "", url, username, password))
            }
        }

    fun addServer(name: String, url: String, username: String, password: String) {
        viewModelScope.launch {
            val server = Server(
                id = UUID.randomUUID().toString(),
                name = name, url = url, username = username, password = password,
            )
            servers.add(server)
            refresh(server)
        }
    }

    fun removeServer(id: String) = viewModelScope.launch { servers.remove(id) }
    fun setActiveServer(id: String) = viewModelScope.launch { servers.setActive(id) }

    // ── Library ───────────────────────────────────────────────────

    fun refresh(server: Server? = activeServer.value) {
        val target = server ?: return
        if (_refreshing.value) return
        viewModelScope.launch {
            _refreshing.value = true
            _status.value = "Scanning ${target.displayName}…"
            runCatching {
                repository.refresh(target) { done, total ->
                    _status.value = "Scanning ${target.displayName} — $done of $total albums"
                }
            }.onSuccess { count ->
                _status.value = if (count == 0) "That server has no albums" else null
            }.onFailure {
                _status.value = it.message ?: "Could not reach that server"
            }
            _refreshing.value = false
        }
    }

    suspend fun search(query: String): List<SongEntity> {
        val server = activeServer.value ?: return emptyList()
        // The cached hit first so typing feels instant and works with no
        // signal; the server's answer replaces it when it lands.
        val local = repository.searchLocal(server, query)
        return runCatching {
            repository.searchRemote(server, query).songs
                .map { it.toEntity(server.id, System.currentTimeMillis()) }
                .ifEmpty { local }
        }.getOrDefault(local)
    }

    suspend fun playlists() = activeServer.value?.let {
        runCatching { repository.playlists(it) }.getOrDefault(emptyList())
    }.orEmpty()

    suspend fun playlistSongs(playlistId: String) = activeServer.value?.let {
        runCatching { repository.playlistSongs(it, playlistId) }.getOrDefault(emptyList())
    }.orEmpty()

    // ── Playing ───────────────────────────────────────────────────

    fun play(songs: List<SongEntity>, startAt: Int = 0) {
        val server = activeServer.value ?: return
        val current = settings.value
        val metered = !Network.unmetered(getApplication())

        // Streaming on mobile data when the user asked us not to. Downloaded
        // tracks still play — they cost nothing — so the queue is narrowed to
        // those rather than refusing outright.
        if (current.wifiOnlyStreaming && metered) {
            val offline = songs.filter { it.downloaded }
            if (offline.isEmpty()) {
                _status.value = "Streaming is set to wifi only, and none of this is downloaded"
                return
            }
            val start = offline.indexOfFirst { it.id == songs.getOrNull(startAt)?.id }
            _status.value = "Wifi only — playing the ${offline.size} downloaded of these"
            player.play(server, offline, start.coerceAtLeast(0)) {
                repository.streamUrl(server, it.id, bitrateFor(current, metered))
            }
            return
        }

        player.play(server, songs, startAt) {
            repository.streamUrl(server, it.id, bitrateFor(current, metered))
        }
        if (current.scrobble) {
            songs.getOrNull(startAt)?.let { song ->
                viewModelScope.launch { repository.scrobble(server, song.id) }
            }
        }
    }

    /**
     * The bitrate ceiling to ask the server for.
     *
     * Only applied on a metered connection. On wifi there is no reason to ask
     * for a worse copy of music you already own, and a ceiling that applied
     * everywhere would quietly degrade the case it was never meant to affect.
     */
    private fun bitrateFor(current: Settings, metered: Boolean) =
        if (metered) current.maxBitrate else 0

    fun coverUrl(coverArt: String?, size: Int = 512): String? =
        activeServer.value?.let { repository.coverUrl(it, coverArt, size) }

    fun toggleStar(song: SongEntity) {
        val server = activeServer.value ?: return
        viewModelScope.launch { repository.setStarred(server, song.id, !song.starred) }
    }

    // ── Downloads ─────────────────────────────────────────────────

    fun download(songs: List<SongEntity>) {
        val server = activeServer.value ?: return
        val app = getApplication<Application>()
        viewModelScope.launch {
            songs.forEach { song ->
                DownloadStore.download(
                    app,
                    DownloadStore.mediaId(server.id, song.id),
                    repository.downloadUrl(server, song.id),
                    song.title,
                )
                repository.markDownloaded(server, song.id, true)
            }
            _status.value = if (songs.size == 1) "Downloading ${songs.first().title}"
            else "Downloading ${songs.size} tracks"
        }
    }

    /** Download this song, or remove it if it is already here. */
    fun toggleDownload(song: SongEntity) {
        if (song.downloaded) removeDownload(song) else download(listOf(song))
    }

    fun removeDownload(song: SongEntity) {
        val server = activeServer.value ?: return
        val app = getApplication<Application>()
        viewModelScope.launch {
            DownloadStore.remove(app, DownloadStore.mediaId(server.id, song.id))
            repository.markDownloaded(server, song.id, false)
        }
    }

    // ── YouTube ───────────────────────────────────────────────────

    private fun interests() = settings.value.let {
        Interests(it.interests, it.blocked, it.blockedChannels, it.filterSlop)
    }

    /** Which feeds are being built. A set, so building one cannot cancel the other. */
    private val building = mutableSetOf<Boolean>()

    fun buildFeed(shorts: Boolean) {
        // Guarded per feed rather than globally: a single flag meant asking for
        // both after an import silently dropped the second one, which is why
        // the Shorts tab stayed empty after importing a history.
        synchronized(building) { if (!building.add(shorts)) return }
        viewModelScope.launch {
            _buildingFeed.value = true
            runCatching { recommender.rebuild(shorts, interests()) }
                .onFailure { _status.value = "Could not build the feed" }
            synchronized(building) {
                building.remove(shorts)
                _buildingFeed.value = building.isNotEmpty()
            }
        }
    }

    suspend fun searchYouTube(query: String, shorts: Boolean = false): List<Video> =
        YouTubeSource.search(query, shorts)

    /** Record a view, which is what everything downstream is built from. */
    fun watched(video: FeedEntity, completion: Float = 0f) {
        viewModelScope.launch {
            youtubeDao.watched(WatchEntity(
                videoId = video.videoId, title = video.title, channel = video.channel,
                channelId = video.channelId, isShort = video.isShort,
                watchedAt = System.currentTimeMillis(), completion = completion,
            ))
            // Out of the feed immediately: seeing something you just watched
            // sitting at the top is the complaint that started all of this.
            youtubeDao.dropFromFeed(video.videoId)
        }
    }

    fun setOpinion(video: FeedEntity, liked: Boolean?) {
        viewModelScope.launch {
            if (liked == null) youtubeDao.clearOpinion(video.videoId)
            else youtubeDao.setOpinion(OpinionEntity(
                videoId = video.videoId, title = video.title, channel = video.channel,
                channelId = video.channelId, liked = liked, at = System.currentTimeMillis(),
            ))
            if (liked == false) youtubeDao.dropFromFeed(video.videoId)
        }
    }

    fun follow(channelId: String, name: String, url: String) {
        viewModelScope.launch {
            youtubeDao.follow(ChannelEntity(
                id = channelId, name = name, url = url, avatar = null,
                followedAt = System.currentTimeMillis(),
            ))
        }
    }

    fun unfollow(channelId: String) = viewModelScope.launch { youtubeDao.unfollow(channelId) }
    fun setMuted(channelId: String, muted: Boolean) =
        viewModelScope.launch { youtubeDao.setMuted(channelId, muted) }

    // ── Settings ──────────────────────────────────────────────────

    fun setTheme(key: String) = viewModelScope.launch { settingsStore.setTheme(key) }
    fun setStyle(key: String) = viewModelScope.launch { settingsStore.setStyle(key) }
    fun setScrobble(on: Boolean) = viewModelScope.launch { settingsStore.setScrobble(on) }
    fun setFilterSlop(on: Boolean) = viewModelScope.launch { settingsStore.setFilterSlop(on) }
    fun setMusicOnly(on: Boolean) = viewModelScope.launch { settingsStore.setMusicOnly(on) }
    fun setMaxBitrate(kbps: Int) = viewModelScope.launch { settingsStore.setMaxBitrate(kbps) }
    fun setWifiOnlyStreaming(on: Boolean) =
        viewModelScope.launch { settingsStore.setWifiOnlyStreaming(on) }
    fun setInterests(values: Set<String>) = viewModelScope.launch { settingsStore.setInterests(values) }
    fun setBlocked(values: Set<String>) = viewModelScope.launch { settingsStore.setBlocked(values) }
    fun setBlockedChannels(values: Set<String>) =
        viewModelScope.launch { settingsStore.setBlockedChannels(values) }

    fun setDownloadOnMobile(on: Boolean) = viewModelScope.launch {
        settingsStore.setDownloadOnMobile(on)
        DownloadStore.setAllowMobileData(getApplication(), on)
    }

    // ── Browse ────────────────────────────────────────────────────

    /**
     * Music near what you already listen to. A shelf per reason.
     *
     * **Music, not video.** Watch is the tab for video; a second one showing
     * the same thing under a different name is worth nothing. So this asks
     * about the artists in your own library first and searches for songs by
     * them, and only falls back to watch-history topics when there is no
     * library to go on.
     */
    suspend fun browse(): List<Pair<String, List<Video>>> {
        val artists = songs.value
            .groupingBy { it.artist }
            .eachCount()
            .entries
            .sortedByDescending { it.value }
            .map { it.key }
            .filter { it.isNotBlank() && !it.equals("Unknown artist", ignoreCase = true) }
            .take(6)

        val terms: List<Pair<String, String>> =
            if (artists.isNotEmpty()) {
                artists.map { it to "More from $it" }
            } else {
                // No library yet. Topics are a poorer seed for music — they
                // come from a watch history that is mostly video — but an
                // empty shelf is worse.
                val history = youtubeDao.recent(shorts = false, limit = 200)
                deriveTopics(history.map { it.title }).take(4)
                    .map { it to "Because you watch about $it" }
            }
        if (terms.isEmpty()) return emptyList()

        return coroutineScope {
            terms.map { (term, heading) ->
                async {
                    // "song" narrows YouTube's results towards music. Without
                    // it, a search returns interviews and reaction videos and
                    // this stops being a music tab.
                    val found = YouTubeSource.search("$term song", limit = 12)
                        .filter { it.durationSeconds in 1..MUSIC_MAX_SECONDS }
                        .distinctBy { it.title.lowercase() }
                    heading to keep(
                        found, interests(),
                        title = { it.title }, channel = { it.channel },
                    )
                }
            }.awaitAll().filter { it.second.isNotEmpty() }
        }
    }

    // ── YouTube as music ──────────────────────────────────────────

    fun playYouTubeAudio(video: Video) {
        viewModelScope.launch {
            _status.value = "Finding audio for ${video.title}…"
            val playable = YouTubeSource.audioStream(video.url)
            if (playable == null) {
                _status.value = "Could not get audio for that"
                return@launch
            }
            player.playUrl(playable.url, video.title, video.channel)
            _status.value = null
        }
    }

    fun downloadYouTubeAudio(video: Video) {
        val app = getApplication<Application>()
        viewModelScope.launch {
            _status.value = "Finding audio for ${video.title}…"
            val playable = YouTubeSource.audioStream(video.url)
            if (playable == null) {
                _status.value = "Could not get audio for that"
                return@launch
            }
            DownloadStore.download(app, "yt:" + video.id, playable.url, video.title)
            _status.value = "Downloading ${video.title}"
        }
    }

    // ── Imports ───────────────────────────────────────────────────

    suspend fun importTakeout(uri: Uri): String {
        val result = Imports.takeout(getApplication(), uri)
        // A fresh history is worth nothing until something is built from it —
        // and *both* feeds, because the point of importing is opening the app
        // to something rather than to two empty tabs.
        if (result.added > 0) {
            buildFeed(shorts = false)
            buildFeed(shorts = true)
        }
        return result.message
    }

    suspend fun importSpotify(url: String): String {
        val tracks = Imports.spotifyPlaylist(url)
        if (tracks.isEmpty()) {
            return "Nothing came back. Public playlists only, and Spotify refuses " +
                "anonymous reads past 100 tracks — try an Exportify CSV."
        }
        val capped = if (tracks.size >= 100)
            " That is Spotify's anonymous limit, so the playlist may be longer — " +
                "an Exportify CSV imports all of it." else ""
        return resolve(tracks) + capped
    }

    suspend fun importExportify(uri: Uri): String {
        val tracks = Imports.exportifyCsv(getApplication(), uri)
        if (tracks.isEmpty()) return "No tracks in that CSV — is it an Exportify export?"
        return resolve(tracks)
    }

    /**
     * Find each imported track and download it.
     *
     * The library on your server is tried first, because a track you already
     * own should not be fetched off YouTube — and matching there is reliable,
     * since the server has real tags. Anything missing falls through to
     * YouTube Music.
     *
     * **What it could not find is listed rather than dropped.** An importer
     * that quietly loses a tenth of a playlist is worse than one that fails
     * loudly, because you find out months later when the song does not play.
     */
    private suspend fun resolve(tracks: List<Imports.Track>): String {
        val app = getApplication<Application>()
        val server = activeServer.value

        var owned = 0
        var fetched = 0
        val missing = mutableListOf<String>()

        tracks.forEach { track ->
            _status.value = "Finding ${track.title}…"
            val query = listOf(track.artist, track.title).filter { it.isNotBlank() }
                .joinToString(" ")

            // Already on the server?
            val local = server?.let {
                runCatching { repository.searchLocal(it, track.title) }.getOrDefault(emptyList())
            }.orEmpty().firstOrNull { candidate ->
                track.artist.isBlank() ||
                    candidate.artist.contains(track.artist, ignoreCase = true) ||
                    track.artist.contains(candidate.artist, ignoreCase = true)
            }
            if (local != null) {
                owned++
                return@forEach
            }

            val match = YouTubeSource.search(query, limit = 1).firstOrNull()
            if (match == null) {
                missing += query
                return@forEach
            }

            val playable = YouTubeSource.audioStream(match.url)
            if (playable == null) {
                missing += query
            } else {
                DownloadStore.download(app, "yt:" + match.id, playable.url, match.title)
                fetched++
            }
        }

        _status.value = null
        return buildString {
            append("$owned already in your library, $fetched downloaded")
            if (missing.isNotEmpty()) {
                append(", ${missing.size} not found:\n")
                append(missing.take(10).joinToString("\n") { "· $it" })
                if (missing.size > 10) append("\n…and ${missing.size - 10} more")
            }
            append(".")
        }
    }

    // ── Visualiser ────────────────────────────────────────────────

    fun setVisualiserLayers(layers: List<Layer>) =
        viewModelScope.launch { settingsStore.setVisualiserLayers(layers) }

    fun setVisualiserIntensity(value: Float) =
        viewModelScope.launch { settingsStore.setVisualiserIntensity(value) }

    fun setVisualiserColours(colours: List<Int>) =
        viewModelScope.launch { settingsStore.setVisualiserColours(colours) }

    fun clearStatus() { _status.value = null }

    private companion object {
        /** Longer than this is a set, a mix or a documentary, not a track. */
        const val MUSIC_MAX_SECONDS = 720L
    }
}
