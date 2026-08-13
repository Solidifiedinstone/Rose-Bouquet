package dev.rose.bouquet.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.util.UnstableApi
import dev.rose.bouquet.data.MusicRepository
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
import dev.rose.bouquet.youtube.Interests
import dev.rose.bouquet.youtube.Recommender
import dev.rose.bouquet.youtube.Video
import dev.rose.bouquet.youtube.YouTubeSource
import kotlinx.coroutines.Dispatchers
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
        player.play(server, songs, startAt) { repository.streamUrl(server, it.id) }
        if (settings.value.scrobble) {
            songs.getOrNull(startAt)?.let { song ->
                viewModelScope.launch { repository.scrobble(server, song.id) }
            }
        }
    }

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

    fun buildFeed(shorts: Boolean) {
        if (_buildingFeed.value) return
        viewModelScope.launch {
            _buildingFeed.value = true
            runCatching { recommender.rebuild(shorts, interests()) }
                .onFailure { _status.value = "Could not build the feed" }
            _buildingFeed.value = false
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
    fun setInterests(values: Set<String>) = viewModelScope.launch { settingsStore.setInterests(values) }
    fun setBlocked(values: Set<String>) = viewModelScope.launch { settingsStore.setBlocked(values) }
    fun setBlockedChannels(values: Set<String>) =
        viewModelScope.launch { settingsStore.setBlockedChannels(values) }

    fun setDownloadOnMobile(on: Boolean) = viewModelScope.launch {
        settingsStore.setDownloadOnMobile(on)
        DownloadStore.setAllowMobileData(getApplication(), on)
    }

    fun clearStatus() { _status.value = null }
}
