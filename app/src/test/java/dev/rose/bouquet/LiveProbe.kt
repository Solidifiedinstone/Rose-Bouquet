package dev.rose.bouquet

import dev.rose.bouquet.youtube.NewPipeDownloader
import org.junit.Test
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

/**
 * Asks YouTube what it is actually serving today.
 *
 * Ignored by default because it hits the network and its answers change
 * without anyone touching this repository — which is exactly why it exists.
 * Three things it has already settled, each of which had produced a wrong fix
 * from reasoning alone:
 *
 * - `isShortFormContent` is false for every search result, shorts included, so
 *   filtering on it returns nothing at all.
 * - A typical video offers one progressive stream at 360p against thirteen
 *   video-only streams up to 1440p, so progressive-first means 360p forever.
 * - Channels only have a shorts tab if they post shorts, so channel uploads
 *   cannot be the only source of a shorts feed.
 *
 * Run it when the YouTube half misbehaves, before changing anything:
 *
 *     ./gradlew testDebugUnitTest --tests "*LiveProbe" --rerun-tasks
 */
@org.junit.Ignore("Hits the network; run by name when diagnosing the YouTube half")
class LiveProbe {
    @Test
    fun probe() {
        NewPipe.init(NewPipeDownloader(), Localization.DEFAULT, ContentCountry.DEFAULT)
        val yt = ServiceList.YouTube

        runCatching {
            val h = yt.searchQHFactory.fromQuery("boards of canada", listOf("videos"), "")
            val items = SearchInfo.getInfo(yt, h).relatedItems.filterIsInstance<StreamInfoItem>()
            println("SEARCH ok: ${items.size} items; shorts=${items.count { it.isShortFormContent }}")
            items.take(2).forEach { println("   ${it.name} | ${it.url} | short=${it.isShortFormContent}") }

            val first = items.firstOrNull()
            if (first != null) {
                val info = StreamInfo.getInfo(yt, first.url)
                println("STREAM ok: video=${info.videoStreams.size} audio=${info.audioStreams.size}")
                println("   bestVideo=${info.videoStreams.filter{it.content.isNotBlank()}.maxByOrNull{it.height}?.height}")
                println("   bestAudio=${info.audioStreams.filter{it.content.isNotBlank()}.maxByOrNull{it.averageBitrate}?.averageBitrate}")
            }
        }.onFailure { println("SEARCH/STREAM FAILED: $it") }

        runCatching {
            kotlinx.coroutines.runBlocking {
                val found = dev.rose.bouquet.youtube.YouTubeSource.search(
                    "music", shorts = true, limit = 6)
                println("SHORTS SEARCH: ${found.size} found")
                val urls = found.map { "https://www.youtube.com/shorts/${it.id}" }

                if (urls.isNotEmpty()) {
                    var t = System.currentTimeMillis()
                    val first = dev.rose.bouquet.youtube.YouTubeSource.videoPlayback(urls[0], 720)
                    val cold = System.currentTimeMillis() - t
                    t = System.currentTimeMillis()
                    dev.rose.bouquet.youtube.YouTubeSource.videoPlayback(urls[0], 720)
                    val warm = System.currentTimeMillis() - t
                    println("RESOLVE: cold=${cold}ms warm=${warm}ms playable=${first != null}")

                    if (urls.size > 2) {
                        t = System.currentTimeMillis()
                        dev.rose.bouquet.youtube.YouTubeSource.prefetch(urls.drop(1).take(2), 720)
                        val pre = System.currentTimeMillis() - t
                        t = System.currentTimeMillis()
                        val hit = dev.rose.bouquet.youtube.YouTubeSource.cached(urls[1], 720)
                        println("PREFETCH: ${pre}ms for 2; cached() lookup=${System.currentTimeMillis()-t}ms hit=${hit != null}")
                    }
                }
            }
        }.onFailure { println("SHORTS PIPELINE FAILED: $it") }

        runCatching {
            val h = yt.searchQHFactory.fromQuery("boards of canada #shorts", listOf("videos"), "")
            val items = SearchInfo.getInfo(yt, h).relatedItems.filterIsInstance<StreamInfoItem>()
            println("HASHSHORTS: ${items.size} items; flaggedShort=${items.count { it.isShortFormContent }}; under3min=${items.count { it.duration in 1..180 }}")
            items.take(3).forEach { println("   ${it.duration}s ${it.name} short=${it.isShortFormContent}") }
        }.onFailure { println("HASHSHORTS FAILED: $it") }

        runCatching {
            val h = yt.searchQHFactory.fromQuery("boards of canada", listOf("videos"), "")
            val first = SearchInfo.getInfo(yt, h).relatedItems.filterIsInstance<StreamInfoItem>().first()
            val info = StreamInfo.getInfo(yt, first.url)
            println("ADAPTIVE: videoOnly=${info.videoOnlyStreams.size} progressive=${info.videoStreams.size}")
            println("   bestVideoOnly=${info.videoOnlyStreams.filter{it.content.isNotBlank()}.maxByOrNull{it.height}?.height}")
            info.videoOnlyStreams.take(3).forEach { println("   vo ${it.height}p ${it.format?.mimeType}") }
            info.audioStreams.take(2).forEach { println("   au ${it.averageBitrate} ${it.format?.mimeType}") }
        }.onFailure { println("ADAPTIVE FAILED: $it") }

        runCatching {
            val ch = ChannelInfo.getInfo(yt, "https://www.youtube.com/@MrBeast")
            println("CHANNEL ok: ${ch.name} tabs=${ch.tabs.map { it.contentFilters }}")
            val shortsTab = ch.tabs.firstOrNull { it.contentFilters.contains(ChannelTabs.SHORTS) }
            val vidTab = ch.tabs.firstOrNull { it.contentFilters.contains(ChannelTabs.VIDEOS) }
            println("   videos tab: ${vidTab != null}, shorts tab: ${shortsTab != null}")
            vidTab?.let {
                val n = ChannelTabInfo.getInfo(yt, it).relatedItems.filterIsInstance<StreamInfoItem>().size
                println("   uploads returned: $n")
            }
        }.onFailure { println("CHANNEL FAILED: $it") }
    }
}
