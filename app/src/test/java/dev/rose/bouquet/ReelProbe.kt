package dev.rose.bouquet

import dev.rose.bouquet.youtube.NewPipeDownloader
import org.junit.Test
import org.schabi.newpipe.extractor.NewPipe
import org.schabi.newpipe.extractor.ServiceList
import org.schabi.newpipe.extractor.localization.ContentCountry
import org.schabi.newpipe.extractor.localization.Localization
import org.schabi.newpipe.extractor.search.SearchInfo
import org.schabi.newpipe.extractor.stream.StreamInfo
import org.schabi.newpipe.extractor.stream.StreamInfoItem
import java.net.HttpURLConnection
import java.net.URL

/**
 * Where a shorts swipe actually spends its time.
 *
 * Written because "the reel is slow" has two possible causes with opposite
 * fixes — resolving the page, or fetching the video — and guessing wrong means
 * building the wrong machinery. What it measured, against YouTube:
 *
 *     resolve=1389ms  video(720p 400KB)=190ms  audio(200KB)=117ms
 *
 * Resolution is seven times the cost of the video itself, so prefetching
 * *resolutions* is what makes the reel feel instant, and preloading media into
 * the player is a second-order improvement worth perhaps 200 ms.
 *
 * Ignored by default because it hits the network. Run it before changing
 * anything about reel timing, since the numbers are YouTube's, not ours, and
 * they move:
 *
 *     ./gradlew testDebugUnitTest --tests "*ReelProbe" --rerun-tasks
 */
@org.junit.Ignore("Hits the network; run by name when the reel feels slow")
class ReelProbe {

    @Test
    fun probe() {
        NewPipe.init(NewPipeDownloader(), Localization.DEFAULT, ContentCountry.DEFAULT)
        val yt = ServiceList.YouTube
        val handler = yt.searchQHFactory.fromQuery("shorts funny", listOf("videos"), "")
        val all = SearchInfo.getInfo(yt, handler).relatedItems.filterIsInstance<StreamInfoItem>()
        val items = all.filter { it.duration in 1..180 }.ifEmpty { all }.take(2)
        println("PROBE candidates=${items.size} of ${all.size}")

        items.forEach { item ->
            val started = System.currentTimeMillis()
            val info = StreamInfo.getInfo(yt, item.url)
            val resolveMs = System.currentTimeMillis() - started

            val video = info.videoOnlyStreams
                .filter { it.content.isNotBlank() && it.height in 1..720 }
                .maxByOrNull { it.height }
            val audio = info.audioStreams.filter { it.content.isNotBlank() }
                .maxByOrNull { it.averageBitrate }
            if (video == null) {
                println("PROBE ${item.name?.take(30)} | no video-only stream")
                return@forEach
            }

            val videoStarted = System.currentTimeMillis()
            val videoBytes = fetch(video.content, 400_000)
            val videoMs = System.currentTimeMillis() - videoStarted

            val audioStarted = System.currentTimeMillis()
            val audioBytes = audio?.let { fetch(it.content, 200_000) } ?: 0
            val audioMs = System.currentTimeMillis() - audioStarted

            println(
                "PROBE ${item.name?.take(30)} | resolve=${resolveMs}ms " +
                    "video(${video.height}p ${videoBytes}B)=${videoMs}ms " +
                    "audio(${audioBytes}B)=${audioMs}ms"
            )
        }
    }

    /** The first [bytes] of a stream, the way the player would start reading it. */
    private fun fetch(url: String, bytes: Long): Int {
        val connection = URL(url).openConnection() as HttpURLConnection
        // The CDN serves different bytes to clients it does not recognise.
        connection.setRequestProperty("User-Agent", NewPipeDownloader.USER_AGENT)
        connection.setRequestProperty("Range", "bytes=0-$bytes")
        connection.connectTimeout = 15_000
        connection.readTimeout = 15_000
        return connection.inputStream.use { it.readBytes().size }
    }
}
