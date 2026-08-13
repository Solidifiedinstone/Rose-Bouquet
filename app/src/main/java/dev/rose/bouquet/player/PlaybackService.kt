package dev.rose.bouquet.player

import android.app.PendingIntent
import android.content.Intent
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.cache.CacheDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import dev.rose.bouquet.MainActivity
import okhttp3.OkHttpClient

/**
 * Playback that outlives the screen.
 *
 * A foreground service rather than a player owned by the activity, because
 * music has to keep going when the app is not visible — and because the
 * notification, the lock screen, Bluetooth buttons, Android Auto and the
 * headset all talk to a `MediaSession` and nothing else. This is the phone's
 * answer to the desktop app's MPRIS: one object the rest of the system already
 * knows how to drive.
 */
@UnstableApi
class PlaybackService : MediaSessionService() {

    private var session: MediaSession? = null

    override fun onCreate() {
        super.onCreate()

        val player = ExoPlayer.Builder(this)
            .setMediaSourceFactory(DefaultMediaSourceFactory(dataSourceFactory()))
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                    .setUsage(C.USAGE_MEDIA)
                    .build(),
                // Handle audio focus: duck for a notification, pause for a
                // call, and resume after. Doing this ourselves is how players
                // end up talking over navigation directions.
                /* handleAudioFocus = */ true,
            )
            // A phone loses signal in lifts and tunnels as a matter of course,
            // so a stall is a normal event rather than an error to give up on.
            .setHandleAudioBecomingNoisy(true)
            .build()

        session = MediaSession.Builder(this, player)
            .setSessionActivity(openAppIntent())
            .build()
    }

    /**
     * Tapping the notification returns to the app rather than starting it over.
     *
     * `singleTop` plus this intent means the running activity comes forward
     * with its stack intact.
     */
    private fun openAppIntent(): PendingIntent = PendingIntent.getActivity(
        this, 0,
        Intent(this, MainActivity::class.java).apply {
            addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
        },
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    /**
     * Where bytes come from: downloads, then the stream cache, then the network.
     *
     * Layering the two caches is what makes streaming and downloading the same
     * code path — a downloaded track is simply one whose bytes are already
     * there, so playing it offline needs no separate branch and no second set
     * of URLs that could drift apart.
     *
     * The order and the write rules both matter:
     *
     * - The **download** layer is read-only during playback (no write sink).
     *   Only `DownloadManager` puts things in it, so casual listening cannot
     *   quietly fill a cache that never evicts.
     * - The **stream** layer underneath does write, and is LRU-bounded, so
     *   replaying something recent is free without growing forever.
     */
    private fun dataSourceFactory(): DataSource.Factory {
        val http = DefaultDataSource.Factory(
            this,
            OkHttpDataSource.Factory(OkHttpClient()).setUserAgent(USER_AGENT),
        )

        val streaming = CacheDataSource.Factory()
            .setCache(DownloadStore.streamCache(this))
            .setUpstreamDataSourceFactory(http)
            // A dead server must not poison the cache with an error body, and
            // must not turn a cached track into a failure either.
            .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)

        return CacheDataSource.Factory()
            .setCache(DownloadStore.cache(this))
            .setUpstreamDataSourceFactory(streaming)
            .setCacheWriteDataSinkFactory(null)
            .setFlags(CacheDataSource.FLAG_IGNORE_CACHE_ON_ERROR)
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo) = session

    /**
     * Stop when the user swipes the app away and nothing is playing.
     *
     * A paused player sitting in the notification shade forever is the
     * behaviour people complain about; a playing one being killed by a swipe is
     * the behaviour they complain about more.
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        val player = session?.player
        if (player == null || !player.playWhenReady || player.mediaItemCount == 0) {
            stopSelf()
        }
    }

    override fun onDestroy() {
        session?.run {
            player.release()
            release()
        }
        session = null
        super.onDestroy()
    }

    companion object {
        const val USER_AGENT = "rose-bouquet-android"
    }
}
