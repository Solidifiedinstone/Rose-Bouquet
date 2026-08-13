package dev.rose.bouquet.youtube

import dev.rose.bouquet.data.db.ChannelEntity
import dev.rose.bouquet.data.db.FeedEntity
import dev.rose.bouquet.data.db.RoseDatabase
import dev.rose.bouquet.data.db.WatchEntity
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope

/**
 * What to watch next, decided on this phone.
 *
 * The shape follows the two-stage design every large recommender uses — gather
 * a few hundred plausible candidates from cheap sources, then rank them — but
 * with the objective deliberately different. Ranking for watch time is what
 * produces a feed of bait; this ranks for *resemblance to what you actually
 * chose to watch*, and treats everything else as a tiebreak.
 *
 * Four rules, all learned from the desktop app getting them wrong first:
 *
 * 1. **Video history and shorts history are separate.** A shorts binge must not
 *    rewrite the Watch tab. [WatchEntity.isShort] carries this all the way
 *    through.
 * 2. **Nothing already watched comes back.** Obvious, and the single most
 *    common complaint about the desktop feed before it was fixed.
 * 3. **No one video floods the feed.** One oddity watched once — the famous
 *    industrial clothes press — otherwise became a tenth of the results.
 * 4. **Blocking blocks.** [keep] runs last and only ever removes.
 */
class Recommender(
    private val database: RoseDatabase,
    private val source: YouTubeSource = YouTubeSource,
) {
    private val youtube = database.youtube()

    /** A candidate with the reason it was gathered, before scoring. */
    private data class Candidate(
        val video: Video,
        val source: String,
        val reason: String,
    )

    /**
     * Build a feed and store it.
     *
     * Stored rather than returned so the next cold start draws instantly
     * instead of showing an empty tab until the network answers — the reason
     * the desktop app's feed looked broken for weeks.
     */
    suspend fun rebuild(shorts: Boolean, interests: Interests): List<FeedEntity> {
        // The shorts feed reads both histories; the video feed reads only its
        // own. Asymmetric on purpose — see the class note, rule 1. Without
        // this, a freshly imported history of ordinary videos produces no
        // channels to ask for shorts and the Shorts tab stays empty forever.
        val history =
            if (shorts) youtube.recentAny(limit = HISTORY)
            else youtube.recent(shorts = false, limit = HISTORY)
        val followed = youtube.activeChannels()
        val liked = youtube.opinions(liked = true)
        val topChannels =
            if (shorts) youtube.topChannelsAny()
            else youtube.topChannels(shorts = false)

        val candidates = gather(shorts, history, followed, liked, topChannels)
        val ranked = rank(candidates, shorts, history, followed, interests)

        val feed = ranked.mapIndexed { index, scored ->
            FeedEntity(
                videoId = scored.video.id,
                title = scored.video.title,
                channel = scored.video.channel,
                channelId = scored.video.channelId,
                thumbnail = scored.video.thumbnail,
                durationSeconds = scored.video.durationSeconds,
                viewCount = scored.video.viewCount,
                uploaded = scored.video.uploaded,
                reason = scored.reason,
                score = scored.score,
                rank = index,
                isShort = shorts,
                builtAt = System.currentTimeMillis(),
            )
        }
        youtube.replaceFeed(shorts, feed)
        return feed
    }

    // ── Stage one: gather ─────────────────────────────────────────

    /**
     * Cheap, broad, and parallel.
     *
     * Every source runs at once because they are all network-bound and
     * independent — done in sequence this took the desktop app 35 seconds, and
     * 8 in parallel. Any one of them failing returns an empty list rather than
     * sinking the build.
     */
    private suspend fun gather(
        shorts: Boolean,
        history: List<WatchEntity>,
        followed: List<ChannelEntity>,
        liked: List<dev.rose.bouquet.data.db.OpinionEntity>,
        topChannels: List<dev.rose.bouquet.data.db.ChannelPlays>,
    ): List<Candidate> = coroutineScope {
        val jobs = mutableListOf<kotlinx.coroutines.Deferred<List<Candidate>>>()

        // Channels you follow, newest uploads.
        followed.take(CHANNEL_LIMIT).forEach { channel ->
            jobs += async {
                source.channelVideos(channel.url, shorts = shorts, limit = PER_CHANNEL)
                    .map { Candidate(it, "following", "You follow ${channel.name}") }
            }
        }

        // Channels you watch a lot but never followed. This is most of the
        // value: what somebody actually watches and what they got round to
        // subscribing to are very different lists.
        val followedIds = followed.mapTo(mutableSetOf()) { it.id }
        topChannels.filterNot { it.id in followedIds }.take(CHANNEL_LIMIT).forEach { played ->
            jobs += async {
                source.channelVideos(channelUrl(played.id), shorts = shorts, limit = PER_CHANNEL)
                    .map { Candidate(it, "affinity", "Because you watch this channel") }
            }
        }

        // Topics, from the titles of what was actually watched. Searching for
        // these is what finds channels the user has never seen — the
        // "discover new channels" half.
        val topics = deriveTopics(history.map { it.title } + liked.map { it.title })
        topics.take(TOPIC_SEARCHES).forEach { topic ->
            jobs += async {
                source.search(topic, shorts = shorts, limit = PER_SEARCH)
                    .map { Candidate(it, "discovered", "Because you watch about $topic") }
            }
        }

        jobs.awaitAll().flatten()
    }

    // ── Stage two: rank ───────────────────────────────────────────

    private data class Scored(val video: Video, val score: Double, val reason: String)

    private suspend fun rank(
        candidates: List<Candidate>,
        shorts: Boolean,
        history: List<WatchEntity>,
        followed: List<ChannelEntity>,
        interests: Interests,
    ): List<Scored> {
        val watched = youtube.watchedIds().toSet()
        val disliked = youtube.dislikedIds().toSet()
        val followedIds = followed.mapTo(mutableSetOf()) { it.id }
        val channelPlays = history.groupingBy { it.channelId }.eachCount()
        val wanted = interests.wanted.map { it.lowercase() }

        // Best candidate per video id, so the same video arriving from three
        // sources is one entry carrying its best reason.
        val best = LinkedHashMap<String, Scored>()

        candidates.forEach { candidate ->
            val video = candidate.video
            if (video.id.isBlank()) return@forEach
            if (video.id in watched) return@forEach     // never re-recommend
            if (video.id in disliked) return@forEach

            var score = WEIGHT_BASE

            channelPlays[video.channelId]?.let { plays ->
                score += WEIGHT_AFFINITY * minOf(plays, AFFINITY_CAP) / AFFINITY_CAP.toDouble()
            }
            if (video.channelId in followedIds) score += WEIGHT_FOLLOWING
            if (candidate.source in DISCOVERED) score += WEIGHT_DISCOVERED

            // A stated interest outweighs everything else by design: it is the
            // one signal the user typed in rather than one inferred from them.
            val title = video.title.lowercase()
            if (wanted.any { it.isNotBlank() && it in title }) score += WEIGHT_WANTED

            // Freshness, mildly. Enough that a dead channel does not dominate,
            // not so much that the feed becomes a news ticker.
            if (video.uploaded?.containsAny("hour", "day", "today") == true) score += WEIGHT_FRESH

            val existing = best[video.id]
            if (existing == null || score > existing.score) {
                best[video.id] = Scored(video, score, candidate.reason)
            }
        }

        val ordered = best.values.sortedByDescending { it.score }
        val filtered = keep(ordered, interests, title = { it.video.title }, channel = { it.video.channel })
        return spread(dropNearDuplicates(filtered))
    }

    /**
     * Drop items whose titles are effectively the same.
     *
     * Channels reupload, and a search for a topic returns the same story told
     * by six channels. Three shared leading words is a low bar deliberately —
     * the failure it prevents (a screen of near-identical rows) is far more
     * annoying than the occasional real video it costs.
     */
    private fun dropNearDuplicates(items: List<Scored>): List<Scored> {
        val seen = mutableSetOf<String>()
        return items.filter { scored ->
            val key = words(scored.video.title).take(SAME_TITLE_WORDS).joinToString(" ")
            key.isBlank() || seen.add(key)
        }
    }

    /**
     * Stop any one channel owning the screen.
     *
     * A cap rather than a penalty: a penalty still lets a channel with enough
     * affinity take the top ten, and the top ten is all anybody scrolls.
     * Overflow is not discarded, only pushed down, so a feed built mostly from
     * one channel is still full.
     */
    private fun spread(items: List<Scored>): List<Scored> {
        val cap = maxOf(2, (items.size * CHANNEL_SHARE).toInt())
        val counts = mutableMapOf<String?, Int>()
        val kept = mutableListOf<Scored>()
        val overflow = mutableListOf<Scored>()

        items.forEach { scored ->
            val used = counts.getOrDefault(scored.video.channelId, 0)
            if (used < cap) {
                counts[scored.video.channelId] = used + 1
                kept += scored
            } else {
                overflow += scored
            }
        }
        return kept + overflow
    }

    private fun String.containsAny(vararg needles: String) =
        needles.any { it in this.lowercase() }

    private fun channelUrl(channelId: String) = "https://www.youtube.com/channel/$channelId"

    companion object {
        // Weights carried over from the desktop app, where they were tuned
        // against a real watch history rather than guessed at here.
        private const val WEIGHT_BASE = 0.1
        private const val WEIGHT_AFFINITY = 1.0
        private const val WEIGHT_FOLLOWING = 0.8
        private const val WEIGHT_FRESH = 0.5
        private const val WEIGHT_DISCOVERED = 0.9
        private const val WEIGHT_WANTED = 2.0

        private val DISCOVERED = setOf("discovered", "similar", "related")

        /** Titles sharing this many leading words are treated as the same video. */
        private const val SAME_TITLE_WORDS = 3

        /** No channel may hold more than this share of the feed. */
        private const val CHANNEL_SHARE = 0.2

        /** Watches of one channel beyond which more says nothing new. */
        private const val AFFINITY_CAP = 8

        private const val HISTORY = 300
        private const val CHANNEL_LIMIT = 12
        private const val PER_CHANNEL = 6
        private const val TOPIC_SEARCHES = 6
        private const val PER_SEARCH = 8
    }
}
