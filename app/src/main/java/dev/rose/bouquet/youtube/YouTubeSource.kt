package dev.rose.bouquet.youtube

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import org.schabi.newpipe.extractor.Image
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.ServiceList
import org.schabi.newpipe.extractor.channel.ChannelInfo
import org.schabi.newpipe.extractor.channel.tabs.ChannelTabInfo
import org.schabi.newpipe.extractor.channel.tabs.ChannelTabs
import kotlinx.coroutines.launch
import org.schabi.newpipe.extractor.localization.ContentCountry
import org.schabi.newpipe.extractor.localization.Localization
import org.schabi.newpipe.extractor.search.SearchInfo
import org.schabi.newpipe.extractor.stream.StreamInfo
import org.schabi.newpipe.extractor.stream.StreamInfoItem
import java.util.concurrent.atomic.AtomicBoolean

/**
 * A video, as everything above this layer sees one.
 *
 * A plain data class rather than the extractor's own types on purpose: the
 * extractor's objects are not serialisable in a useful way, carry a large
 * object graph, and change shape between releases. Converting once at the
 * boundary means a NewPipe upgrade touches this file and nothing else.
 */
data class Video(
    val id: String,
    val title: String,
    val url: String,
    val channel: String,
    val channelUrl: String?,
    val thumbnail: String?,
    val durationSeconds: Long,
    val viewCount: Long,
    val uploaded: String?,
    val isShort: Boolean,
) {
    /** Channel id parsed out of the channel URL, for grouping and blocking. */
    val channelId: String? get() = channelUrl?.substringAfterLast('/')?.takeIf { it.isNotBlank() }
}

data class Channel(
    val id: String,
    val name: String,
    val url: String,
    val avatar: String?,
    val subscribers: Long,
    val description: String?,
)

/** A playable stream: a URL and enough to know what it is. */
data class Playable(
    val url: String,
    val mimeType: String?,
    val isVideo: Boolean,
    val height: Int = 0,
)

/**
 * A video to play.
 *
 * [audioUrl] is null only for a progressive stream, where the one file carries
 * sound as well. Otherwise the two are separate and the player merges them.
 */
data class VideoPlayback(
    val videoUrl: String,
    val audioUrl: String?,
    val height: Int,
) {
    val adaptive: Boolean get() = audioUrl != null
}

/**
 * YouTube, via NewPipeExtractor.
 *
 * No API key and no account, which is the point — the desktop app makes the
 * same trade with yt-dlp. What comes back is what a browser would be served,
 * parsed.
 *
 * **Everything here blocks and everything here can fail.** The extractor does
 * synchronous network IO and throws on anything from a parse change to a
 * captcha, so every method is `suspend`, runs on the IO dispatcher, and returns
 * an empty result rather than propagating a parse failure into the interface.
 * YouTube changes its pages without warning; a tab that comes up empty is a bad
 * day, and a tab that crashes the app is a bug report.
 */
object YouTubeSource {

    private val started = AtomicBoolean(false)

    /**
     * Stream URLs already resolved, so a reel does not wait for the network.
     *
     * Resolving one short means fetching and parsing a YouTube page, which is
     * most of a second. Doing that on the swipe is what made the reel feel
     * broken; the desktop app solved the same problem with a stream cache and a
     * prefetch, and this is that.
     *
     * Bounded, because a long doomscroll would otherwise hold every URL of the
     * evening. Entries expire because YouTube's URLs are signed and time
     * limited — a stale one is a 403 rather than a slow load.
     */
    private val resolved = java.util.concurrent.ConcurrentHashMap<String, Timed>()

    private class Timed(val playback: VideoPlayback, val at: Long)

    private val service get() = ServiceList.YouTube

    /** Safe to call repeatedly and from anywhere; only the first call does work. */
    fun start() {
        if (started.compareAndSet(false, true)) {
            // The phone's own language and country.
            //
            // Hardcoding the extractor's DEFAULT told YouTube nothing about who
            // was asking, and YouTube answered with whatever it serves an
            // unidentified client — which is how a feed ends up in Mandarin for
            // somebody who has never watched a word of it. This is the same
            // thing a browser sends.
            val locale = java.util.Locale.getDefault()
            NewPipe.init(
                NewPipeDownloader(),
                Localization.fromLocale(locale),
                locale.country.takeIf { it.isNotBlank() }
                    ?.let { ContentCountry(it) } ?: ContentCountry.DEFAULT,
            )
        }
    }

    // ── Searching ─────────────────────────────────────────────────

    /**
     * Search for videos.
     *
     * [shorts] filters on the extractor's own short-form flag rather than on
     * duration or on a "#shorts" string in the query. Duration is wrong — a
     * 50-second music video is not a short — and the string hack returns
     * whatever happens to be tagged.
     */
    suspend fun search(query: String, shorts: Boolean = false, limit: Int = 30): List<Video> =
        io(emptyList()) {
            // `#shorts` in the query is how the desktop app finds shorts, and
            // measurement says it is the only thing that works: YouTube's
            // search results come back with `isShortFormContent` false for
            // every single item, shorts included. Filtering on that flag
            // discarded the entire result set and left the Shorts tab
            // permanently empty. Duration is the usable signal instead.
            val text = if (shorts && "#shorts" !in query.lowercase()) "$query #shorts" else query
            val handler = service.searchQHFactory.fromQuery(text, listOf(VIDEOS), "")

            SearchInfo.getInfo(service, handler)
                .relatedItems
                .filterIsInstance<StreamInfoItem>()
                .filter { item ->
                    if (shorts) item.isShortFormContent || item.duration in 1..SHORT_SECONDS
                    // A shorts search leaking into the video feed is worse than
                    // the reverse, so the video side excludes the flagged ones.
                    else !item.isShortFormContent
                }
                .take(limit)
                .map { it.toVideo(forceShort = shorts) }
        }

    // ── One video ─────────────────────────────────────────────────

    suspend fun video(url: String): StreamInfo? = io(null) { StreamInfo.getInfo(service, url) }

    /**
     * The best audio-only stream, for playing a video as music.
     *
     * Highest bitrate rather than a fixed format: YouTube offers different
     * codecs to different clients and hardcoding one is how a client stops
     * working on a Tuesday.
     */
    suspend fun audioStream(url: String): Playable? = io(null) {
        StreamInfo.getInfo(service, url).audioStreams
            .filter { it.content.isNotBlank() }
            .maxByOrNull { it.averageBitrate }
            ?.let { Playable(it.content, it.format?.mimeType, isVideo = false) }
    }

    /**
     * What to play for a video: a picture track and a sound track.
     *
     * Adaptive first, progressive only as a fallback. That is the opposite of
     * what looks simpler, and the measurement is why: a typical video now
     * offers **one** progressive stream at 360p against **thirteen**
     * video-only streams up to 1440p. Preferring progressive meant everything
     * played at 360p when it played at all, which is most of "the videos do
     * not work".
     *
     * Adaptive means two URLs that the player has to combine, which is a
     * MergingMediaSource on the other side — see the Watch screen.
     */
    /**
     * Resolve ahead of time, so the next swipe costs nothing.
     *
     * Fire and forget: the caller does not wait, and a failure here simply
     * means the reel resolves that one the slow way when it arrives.
     */
    fun prefetch(urls: List<String>, maxHeight: Int = 720) {
        urls.filter { !resolved.containsKey(cacheKey(it, maxHeight)) }
            .take(PREFETCH)
            .forEach { url ->
                val key = cacheKey(url, maxHeight)
                // One in flight per URL. Without this, every swipe queued
                // another resolve of shorts already being fetched.
                if (!inFlight.add(key)) return@forEach
                scope.launch {
                    try {
                        videoPlayback(url, maxHeight)
                    } finally {
                        inFlight.remove(key)
                    }
                }
            }
    }

    /**
     * Prefetching lives here rather than in the screen's coroutine scope.
     *
     * A reel cancels its scope on every swipe, so a prefetch started from there
     * was killed by the next swipe — which meant a steady scroll cancelled
     * every fetch just before it finished and nothing was ever cached. The work
     * has to outlive the page that asked for it, which is the whole point of
     * prefetching.
     */
    private val scope = kotlinx.coroutines.CoroutineScope(
        kotlinx.coroutines.SupervisorJob() + Dispatchers.IO)

    private val inFlight = java.util.Collections.newSetFromMap(
        java.util.concurrent.ConcurrentHashMap<String, Boolean>())

    private fun cacheKey(url: String, maxHeight: Int) = "$url|$maxHeight"

    /**
     * An already-resolved stream, without touching the network or a dispatcher.
     *
     * Lets the reel tell "prefetched, play it now" from "not ready, show a
     * spinner". Going through the suspending path for a cache hit costs a
     * dispatch and makes an instant swipe render a spinner for a frame.
     */
    fun cached(url: String, maxHeight: Int = 1080): VideoPlayback? {
        val hit = resolved[cacheKey(url, maxHeight)] ?: return null
        return if (System.currentTimeMillis() - hit.at < URL_TTL_MS) hit.playback else null
    }

    suspend fun videoPlayback(url: String, maxHeight: Int = 1080): VideoPlayback? = io(null) {
        val key = cacheKey(url, maxHeight)
        val now = System.currentTimeMillis()

        resolved[key]?.let { if (now - it.at < URL_TTL_MS) return@io it.playback }

        if (resolved.size > CACHE_LIMIT) {
            // Cheap and good enough: this is a reel, so anything old is behind
            // the user and will not be asked for again.
            resolved.entries.removeIf { now - it.value.at > URL_TTL_MS }
            if (resolved.size > CACHE_LIMIT) resolved.clear()
        }

        val info = StreamInfo.getInfo(service, url)

        val audio = info.audioStreams
            .filter { it.content.isNotBlank() }
            .maxByOrNull { it.averageBitrate }

        // H.264 in MP4 ahead of VP9/AV1 in WebM at the same height.
        //
        // Every Android device decodes H.264 in hardware; VP9 and AV1 at 1080
        // and above fall back to software on plenty of them, and a software
        // decode that cannot keep up is a video that freezes rather than one
        // that looks worse. Height still wins overall — this only breaks ties.
        val playable = info.videoOnlyStreams
            .filter { it.content.isNotBlank() && it.height in 1..maxHeight }
        val video = playable
            .filter { it.format?.mimeType?.contains("mp4", ignoreCase = true) == true }
            .maxByOrNull { it.height }
            ?: playable.maxByOrNull { it.height }

        if (video != null && audio != null) {
            val found = VideoPlayback(video.content, audio.content, video.height)
            resolved[key] = Timed(found, now)
            return@io found
        }

        // No usable adaptive pair. One file carrying both is worse quality but
        // it is a video that plays.
        info.videoStreams.filter { it.content.isNotBlank() }
            .maxByOrNull { it.height }
            ?.let { VideoPlayback(it.content, null, it.height) }
    }?.also { resolved[cacheKey(url, maxHeight)] = Timed(it, System.currentTimeMillis()) }

    // ── Channels ──────────────────────────────────────────────────

    suspend fun channel(url: String): Channel? = io(null) {
        ChannelInfo.getInfo(service, url).let {
            Channel(
                id = it.id,
                name = it.name,
                url = it.url,
                avatar = it.avatars.best(),
                subscribers = it.subscriberCount,
                description = it.description,
            )
        }
    }

    /**
     * A channel's recent uploads, or its shorts.
     *
     * Channels do not all have both tabs — a channel that has never posted a
     * short has no shorts tab at all — so a missing tab is an empty list, not
     * an error.
     */
    suspend fun channelVideos(url: String, shorts: Boolean = false, limit: Int = 20): List<Video> =
        io(emptyList()) {
            val info = ChannelInfo.getInfo(service, url)
            val wanted = if (shorts) ChannelTabs.SHORTS else ChannelTabs.VIDEOS
            val tab = info.tabs.firstOrNull { it.contentFilters.contains(wanted) }
                ?: return@io emptyList()

            ChannelTabInfo.getInfo(service, tab)
                .relatedItems
                .filterIsInstance<StreamInfoItem>()
                .take(limit)
                .map { it.toVideo() }
        }

    // ── Plumbing ──────────────────────────────────────────────────

    /**
     * Run extractor work off the main thread, and never let it crash a screen.
     *
     * The catch is deliberately broad. NewPipe throws checked extraction
     * exceptions, but a page it did not expect also produces
     * `NullPointerException` and `IndexOutOfBoundsException` from deep inside
     * the parser, and none of those should be able to take the app down —
     * YouTube changing a page is a normal event over the life of an app that
     * scrapes it.
     */
    private suspend fun <T> io(fallback: T, block: suspend () -> T): T =
        withContext(Dispatchers.IO) {
            start()
            try {
                block()
            } catch (e: Exception) {
                android.util.Log.w("YouTubeSource", "extraction failed", e)
                fallback
            }
        }

    private fun StreamInfoItem.toVideo(forceShort: Boolean = false) = Video(
        id = url.videoId(),
        title = name.orEmpty(),
        url = url,
        channel = uploaderName.orEmpty(),
        channelUrl = uploaderUrl,
        thumbnail = thumbnails.best(),
        durationSeconds = duration,
        viewCount = viewCount.coerceAtLeast(0),
        uploaded = textualUploadDate,
        // The flag is unreliable in search results — always false, even for
        // shorts — so a shorts search says what it found.
        isShort = isShortFormContent || forceShort,
    )

    /**
     * The largest thumbnail offered.
     *
     * Images arrive as a list of sizes; picking the biggest and letting the
     * image loader downscale beats picking a middle one and finding it blurry
     * on a modern screen.
     */
    private fun List<Image>.best(): String? =
        maxByOrNull { it.height.toLong() * it.width }?.url ?: firstOrNull()?.url

    private const val VIDEOS = "videos"

    /** Longest a thing can be and still belong in a reel. */
    private const val SHORT_SECONDS = 180

    /** How far ahead the reel resolves. Three is roughly a fast swipe. */
    private const val PREFETCH = 3

    /** YouTube's stream URLs are signed and time limited. */
    private const val URL_TTL_MS = 30 * 60 * 1000L

    private const val CACHE_LIMIT = 120
}

/** The `v=` id out of a watch URL, which is what everything else keys on. */
fun String.videoId(): String =
    substringAfter("v=", "").substringBefore('&').ifBlank {
        // Shorts and youtu.be links put the id in the path instead.
        substringAfterLast('/').substringBefore('?')
    }
