package dev.rose.bouquet.youtube

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.schabi.newpipe.extractor.Image
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.ServiceList
import org.schabi.newpipe.extractor.channel.ChannelInfo
import org.schabi.newpipe.extractor.channel.tabs.ChannelTabInfo
import org.schabi.newpipe.extractor.channel.tabs.ChannelTabs
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

    private val service get() = ServiceList.YouTube

    /** Safe to call repeatedly and from anywhere; only the first call does work. */
    fun start() {
        if (started.compareAndSet(false, true)) {
            NewPipe.init(
                NewPipeDownloader(),
                Localization.DEFAULT,
                ContentCountry.DEFAULT,
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
    suspend fun videoPlayback(url: String, maxHeight: Int = 1080): VideoPlayback? = io(null) {
        val info = StreamInfo.getInfo(service, url)

        val audio = info.audioStreams
            .filter { it.content.isNotBlank() }
            .maxByOrNull { it.averageBitrate }

        val video = info.videoOnlyStreams
            .filter { it.content.isNotBlank() && it.height in 1..maxHeight }
            .maxByOrNull { it.height }

        if (video != null && audio != null) {
            return@io VideoPlayback(video.content, audio.content, video.height)
        }

        // No usable adaptive pair. One file carrying both is worse quality but
        // it is a video that plays.
        info.videoStreams.filter { it.content.isNotBlank() }
            .maxByOrNull { it.height }
            ?.let { VideoPlayback(it.content, null, it.height) }
    }

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
}

/** The `v=` id out of a watch URL, which is what everything else keys on. */
fun String.videoId(): String =
    substringAfter("v=", "").substringBefore('&').ifBlank {
        // Shorts and youtu.be links put the id in the path instead.
        substringAfterLast('/').substringBefore('?')
    }
