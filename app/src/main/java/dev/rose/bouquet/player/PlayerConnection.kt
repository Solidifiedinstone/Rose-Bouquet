package dev.rose.bouquet.player

import android.content.ComponentName
import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.MoreExecutors
import dev.rose.bouquet.data.Server
import dev.rose.bouquet.data.db.SongEntity
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * What is playing, as something the interface can collect.
 *
 * Compose wants state it can observe; Media3 offers a listener interface and a
 * controller you have to poll for position. This is the seam between them, and
 * the only place in the app that knows Media3 exists.
 */
data class PlaybackState(
    val song: SongEntity? = null,
    val playing: Boolean = false,
    val positionMs: Long = 0,
    val durationMs: Long = 0,
    val queue: List<SongEntity> = emptyList(),
    val index: Int = 0,
    val shuffle: Boolean = false,
    val repeat: Int = Player.REPEAT_MODE_OFF,
) {
    val hasNext: Boolean get() = index < queue.lastIndex || repeat != Player.REPEAT_MODE_OFF
    val progress: Float
        get() = if (durationMs > 0) (positionMs.toFloat() / durationMs).coerceIn(0f, 1f) else 0f
}

/**
 * Holds the connection to [PlaybackService] and mirrors it into a StateFlow.
 *
 * One instance for the whole app. Connecting per screen would give each one a
 * controller with its own view of the queue, and they would disagree the first
 * time somebody pressed next on the lock screen.
 */
@UnstableApi
class PlayerConnection(private val context: Context) {

    private var controller: MediaController? = null

    private val _state = MutableStateFlow(PlaybackState())
    val state: StateFlow<PlaybackState> = _state.asStateFlow()

    /**
     * The queue as the app understands it.
     *
     * Media3 knows about `MediaItem`s, which carry only what fits in metadata.
     * Keeping the real rows alongside means the interface can show everything a
     * song has — the album id, whether it is downloaded — without stuffing it
     * all into media metadata and parsing it back out.
     */
    private var queue: List<SongEntity> = emptyList()

    private val listener = object : Player.Listener {
        override fun onEvents(player: Player, events: Player.Events) = publish(player)
    }

    fun connect(onReady: () -> Unit = {}) {
        if (controller != null) return onReady()

        val token = SessionToken(context, ComponentName(context, PlaybackService::class.java))
        val future = MediaController.Builder(context, token).buildAsync()
        future.addListener({
            controller = future.get().also { it.addListener(listener); publish(it) }
            onReady()
        }, MoreExecutors.directExecutor())
    }

    fun release() {
        controller?.removeListener(listener)
        controller?.release()
        controller = null
    }

    private fun publish(player: Player) {
        val index = player.currentMediaItemIndex
        _state.value = PlaybackState(
            song = queue.getOrNull(index),
            playing = player.isPlaying,
            positionMs = player.currentPosition.coerceAtLeast(0),
            // An unprepared or streaming item reports TIME_UNSET; showing that
            // as a duration puts a negative number on the progress bar.
            durationMs = player.duration.takeIf { it > 0 } ?: 0,
            queue = queue,
            index = index,
            shuffle = player.shuffleModeEnabled,
            repeat = player.repeatMode,
        )
    }

    /** Called on a timer while the sheet is open — position does not emit events. */
    fun tick() = controller?.let { publish(it) }

    // ── Commands ──────────────────────────────────────────────────

    fun play(server: Server, songs: List<SongEntity>, startAt: Int = 0, urlFor: (SongEntity) -> String) {
        val player = controller ?: return
        queue = songs
        player.setMediaItems(songs.map { it.toMediaItem(server, urlFor(it)) }, startAt, 0)
        player.prepare()
        player.play()
    }

    fun playPause() {
        val player = controller ?: return
        if (player.isPlaying) player.pause() else player.play()
    }

    fun next() = controller?.seekToNextMediaItem()
    fun previous() {
        val player = controller ?: return
        // Restart the track if we are past the start of it, which is what every
        // other player does and what the button is used for most often.
        if (player.currentPosition > RESTART_THRESHOLD_MS) player.seekTo(0)
        else player.seekToPreviousMediaItem()
    }

    fun seekTo(ms: Long) = controller?.seekTo(ms)
    fun seekToFraction(fraction: Float) {
        val player = controller ?: return
        if (player.duration > 0) player.seekTo((player.duration * fraction).toLong())
    }

    fun setShuffle(on: Boolean) {
        controller?.shuffleModeEnabled = on
    }

    fun cycleRepeat() {
        val player = controller ?: return
        player.repeatMode = when (player.repeatMode) {
            Player.REPEAT_MODE_OFF -> Player.REPEAT_MODE_ALL
            Player.REPEAT_MODE_ALL -> Player.REPEAT_MODE_ONE
            else -> Player.REPEAT_MODE_OFF
        }
    }

    fun jumpTo(index: Int) = controller?.seekTo(index, 0)

    fun stop() {
        controller?.stop()
        controller?.clearMediaItems()
        queue = emptyList()
    }

    companion object {
        private const val RESTART_THRESHOLD_MS = 4_000
    }
}

/**
 * A song as Media3 sees it.
 *
 * The media id is the app's own `serverId:songId` rather than the URL, so a
 * downloaded file is recognised as the same track no matter what URL the server
 * hands out this week — Subsonic URLs carry a per-request salt and token, so
 * the URL of a given song is different every single time it is asked for.
 */
fun SongEntity.toMediaItem(server: Server, url: String): MediaItem = MediaItem.Builder()
    .setMediaId(DownloadStore.mediaId(server.id, id))
    .setUri(url)
    .setMediaMetadata(
        MediaMetadata.Builder()
            .setTitle(title)
            .setArtist(artist)
            .setAlbumTitle(album)
            .setTrackNumber(track)
            .setReleaseYear(year)
            .setIsBrowsable(false)
            .setIsPlayable(true)
            .build()
    )
    .build()
