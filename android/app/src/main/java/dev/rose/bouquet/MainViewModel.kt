package dev.rose.bouquet

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import android.content.ComponentName
import com.google.common.util.concurrent.MoreExecutors
import dev.rose.bouquet.data.Settings
import dev.rose.bouquet.data.SubsonicClient
import dev.rose.bouquet.player.PlaybackService
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Everything the screens read from, and the one place that talks to the server.
 *
 * The player is reached through a `MediaController` bound to the service rather
 * than an `ExoPlayer` held here, so playback belongs to the service and
 * survives this ViewModel being destroyed on a rotation.
 */
class MainViewModel(app: Application) : AndroidViewModel(app) {

    data class UiState(
        val connecting: Boolean = false,
        val connected: Boolean = false,
        val error: String? = null,
        val albums: List<SubsonicClient.Album> = emptyList(),
        val songs: List<SubsonicClient.Song> = emptyList(),
        val openAlbum: SubsonicClient.Album? = null,
        val nowPlaying: SubsonicClient.Song? = null,
        val playing: Boolean = false,
    )

    private val settings = Settings(app)
    private val client = SubsonicClient()

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    val appearance = settings.state

    private var controller: MediaController? = null

    init {
        connectPlayer()
        viewModelScope.launch {
            val saved = settings.state.first()
            if (saved.configured) connect(saved.serverUrl, saved.username, saved.password)
        }
    }

    // ── The player ────────────────────────────────────────────────

    private fun connectPlayer() {
        val token = SessionToken(
            getApplication(),
            ComponentName(getApplication(), PlaybackService::class.java),
        )
        val future = MediaController.Builder(getApplication(), token).buildAsync()
        future.addListener({
            controller = future.get()
        }, MoreExecutors.directExecutor())
    }

    // ── The server ────────────────────────────────────────────────

    private suspend fun server(): SubsonicClient.Server {
        val saved = settings.state.first()
        return SubsonicClient.Server(saved.serverUrl, saved.username, saved.password)
    }

    /** Check an address before saving it, so a typo fails here and not later. */
    fun connect(url: String, username: String, password: String, save: Boolean = false) {
        viewModelScope.launch {
            _state.value = _state.value.copy(connecting = true, error = null)

            val target = SubsonicClient.Server(url.trimEnd('/'), username, password)
            val reachable = withContext(Dispatchers.IO) { client.ping(target) }

            reachable
                .onSuccess {
                    if (save) settings.setServer(url, username, password)
                    _state.value = _state.value.copy(connecting = false, connected = true)
                    loadLibrary()
                }
                .onFailure { failure ->
                    _state.value = _state.value.copy(
                        connecting = false,
                        connected = false,
                        error = failure.message ?: "Could not reach that server",
                    )
                }
        }
    }

    fun loadLibrary() {
        viewModelScope.launch {
            val target = server()
            val albums = withContext(Dispatchers.IO) { client.albums(target) }
            albums.onSuccess { _state.value = _state.value.copy(albums = it, error = null) }
                .onFailure { _state.value = _state.value.copy(error = it.message) }
        }
    }

    fun openAlbum(album: SubsonicClient.Album?) {
        if (album == null) {
            _state.value = _state.value.copy(openAlbum = null, songs = emptyList())
            return
        }
        viewModelScope.launch {
            val target = server()
            val songs = withContext(Dispatchers.IO) { client.albumSongs(target, album.id) }
            songs.onSuccess { _state.value = _state.value.copy(openAlbum = album, songs = it) }
                .onFailure { _state.value = _state.value.copy(error = it.message) }
        }
    }

    fun search(query: String) {
        viewModelScope.launch {
            val target = server()
            val found = withContext(Dispatchers.IO) { client.search(target, query) }
            found.onSuccess { _state.value = _state.value.copy(songs = it, openAlbum = null) }
                .onFailure { _state.value = _state.value.copy(error = it.message) }
        }
    }

    fun shuffleEverything() {
        viewModelScope.launch {
            val target = server()
            withContext(Dispatchers.IO) { client.random(target) }
                .onSuccess { songs -> play(songs, 0) }
                .onFailure { _state.value = _state.value.copy(error = it.message) }
        }
    }

    // ── Playing ───────────────────────────────────────────────────

    fun play(songs: List<SubsonicClient.Song>, index: Int) {
        viewModelScope.launch {
            val target = server()
            val player = controller ?: return@launch

            val items = songs.map { song ->
                MediaItem.Builder()
                    .setUri(client.streamUrl(target, song.id))
                    .setMediaId(song.id)
                    .setMediaMetadata(
                        MediaMetadata.Builder()
                            .setTitle(song.title)
                            .setArtist(song.artist)
                            .setAlbumTitle(song.album)
                            .setArtworkUri(
                                client.coverUrl(target, song.coverArt)?.let(android.net.Uri::parse)
                            )
                            .build()
                    )
                    .build()
            }

            player.setMediaItems(items, index, 0L)
            player.prepare()
            player.play()

            _state.value = _state.value.copy(
                nowPlaying = songs.getOrNull(index), playing = true
            )
        }
    }

    fun togglePlay() {
        val player = controller ?: return
        if (player.isPlaying) player.pause() else player.play()
        _state.value = _state.value.copy(playing = player.isPlaying)
    }

    fun next() {
        controller?.seekToNextMediaItem()
    }

    fun previous() {
        controller?.seekToPreviousMediaItem()
    }

    fun toggleShuffle(): Boolean {
        val player = controller ?: return false
        player.shuffleModeEnabled = !player.shuffleModeEnabled
        return player.shuffleModeEnabled
    }

    fun coverFor(song: SubsonicClient.Song?, size: Int = 512): String? = null.also {
        // Cover URLs need the server, which needs a coroutine; screens call
        // `coverUrl` through `withServer` instead of blocking here.
    }

    suspend fun coverUrl(song: SubsonicClient.Song?, size: Int = 512): String? {
        if (song == null) return null
        return client.coverUrl(server(), song.coverArt, size)
    }

    // ── Appearance ────────────────────────────────────────────────

    fun setAppearance(themeKey: String, styleKey: String) {
        viewModelScope.launch { settings.setAppearance(themeKey, styleKey) }
    }

    fun forgetServer() {
        viewModelScope.launch {
            settings.forgetServer()
            _state.value = UiState()
        }
    }
}
