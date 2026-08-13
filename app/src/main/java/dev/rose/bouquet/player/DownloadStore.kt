package dev.rose.bouquet.player

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.database.StandaloneDatabaseProvider
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.cache.Cache
import androidx.media3.datasource.cache.LeastRecentlyUsedCacheEvictor
import androidx.media3.datasource.cache.NoOpCacheEvictor
import androidx.media3.datasource.cache.SimpleCache
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.offline.Download
import androidx.media3.exoplayer.offline.DownloadManager
import androidx.media3.exoplayer.offline.DownloadRequest
import androidx.media3.exoplayer.scheduler.Requirements
import okhttp3.OkHttpClient
import java.io.File
import java.util.concurrent.Executors

/**
 * What is on the phone, and how it got there.
 *
 * Two caches rather than one, because they have opposite eviction rules and
 * mixing them is how "I downloaded this album for the flight" turns into
 * "the album was evicted to make room for something I streamed once":
 *
 * - **downloads** never evict. The user asked for these; only the user removes
 *   them.
 * - **stream cache** is bounded and evicts least-recently-used, so streaming
 *   stays fast on repeat without slowly consuming the device.
 */
@UnstableApi
object DownloadStore {

    /** Ceiling on the opportunistic stream cache. Downloads are not counted here. */
    private const val STREAM_CACHE_BYTES = 512L * 1024 * 1024

    private var downloadCache: Cache? = null
    private var streamCache: Cache? = null
    private var manager: DownloadManager? = null

    /**
     * The cache the player reads through.
     *
     * Deliberately the *download* cache: a `CacheDataSource` chain checks it
     * first, so a downloaded track plays from disk with no network at all, and
     * anything else falls through to the stream cache and then the network.
     */
    @Synchronized
    fun cache(context: Context): Cache = downloadCache ?: SimpleCache(
        File(context.filesDir, "downloads"),
        // Downloads are removed by the user, never by pressure. An evictor here
        // would silently delete music somebody deliberately kept for a journey.
        NoOpCacheEvictor(),
        StandaloneDatabaseProvider(context),
    ).also { downloadCache = it }

    @Synchronized
    fun streamCache(context: Context): Cache = streamCache ?: SimpleCache(
        File(context.cacheDir, "stream"),
        LeastRecentlyUsedCacheEvictor(STREAM_CACHE_BYTES),
        StandaloneDatabaseProvider(context),
    ).also { streamCache = it }

    /**
     * The download queue.
     *
     * Media3's own manager rather than a hand-rolled one: it survives the
     * process being killed, resumes part-finished files instead of starting
     * over, and already knows how to wait for wifi. A downloader written from
     * scratch gets all three wrong at least once.
     */
    @Synchronized
    fun manager(context: Context): DownloadManager = manager ?: DownloadManager(
        context.applicationContext,
        StandaloneDatabaseProvider(context),
        cache(context),
        DefaultDataSource.Factory(
            context,
            OkHttpDataSource.Factory(OkHttpClient()).setUserAgent(PlaybackService.USER_AGENT),
        ),
        // Three at a time: enough to use the connection, few enough that a
        // download queue does not starve playback of bandwidth.
        Executors.newFixedThreadPool(3),
    ).also {
        it.maxParallelDownloads = 3
        manager = it
    }

    /**
     * Queue a track for offline.
     *
     * [id] is the app's own `serverId:songId`, not the URL — a server that
     * moves house or reissues a token changes every URL it hands out, and a
     * download keyed on one of those would be orphaned by it.
     */
    fun download(context: Context, id: String, url: String, title: String) {
        val request = DownloadRequest.Builder(id, android.net.Uri.parse(url))
            .setData(title.toByteArray())
            .build()
        manager(context).addDownload(request)
    }

    fun remove(context: Context, id: String) = manager(context).removeDownload(id)

    fun removeAll(context: Context) = manager(context).removeAllDownloads()

    /**
     * Whether downloads may run on mobile data.
     *
     * Off by default. Filling somebody's data allowance with a discography
     * they queued on wifi is not a mistake they can undo.
     */
    fun setAllowMobileData(context: Context, allowed: Boolean) {
        manager(context).requirements = Requirements(
            if (allowed) Requirements.NETWORK else Requirements.NETWORK_UNMETERED
        )
    }

    /** Every download the manager knows about, whatever state it is in. */
    fun downloads(context: Context): List<Download> {
        val cursor = manager(context).downloadIndex.getDownloads()
        return buildList {
            cursor.use {
                while (it.moveToNext()) add(it.download)
            }
        }
    }

    /** Media id for a song, stable across URL changes and server renames. */
    fun mediaId(serverId: String, songId: String) = "$serverId:$songId"

    fun MediaItem.roseId(): String = mediaId
}
