package dev.rose.bouquet.data.db

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * A song as the phone last saw it.
 *
 * Cached so the library still browses with no server reachable — which is the
 * point of a phone client. [downloaded] is the difference between "we know
 * this song exists" and "we hold the bytes"; both matter, and conflating them
 * produces an offline library full of things that will not play.
 *
 * Keyed on server *and* song id, because two servers can and do use the same
 * ids for different music.
 */
@Entity(
    tableName = "songs",
    primaryKeys = ["serverId", "id"],
    indices = [
        Index("serverId", "albumId"),
        Index("serverId", "downloaded"),
        // The library list sorts by these on every read of a whole library.
        // Without it SQLite sorts the entire table in memory each time.
        Index("serverId", "artist", "album", "track"),
    ],
)
data class SongEntity(
    val serverId: String,
    val id: String,
    val title: String,
    val artist: String,
    val album: String,
    val albumId: String?,
    val durationSeconds: Int,
    val track: Int?,
    val year: Int?,
    val coverArt: String?,
    val suffix: String?,
    val sizeBytes: Long?,
    val starred: Boolean,
    val downloaded: Boolean = false,
    /** When this row was last refreshed from the server. */
    val seenAt: Long = 0,
)

@Entity(
    tableName = "albums",
    primaryKeys = ["serverId", "id"],
    indices = [Index("serverId", "name")],
)
data class AlbumEntity(
    val serverId: String,
    val id: String,
    val name: String,
    val artist: String,
    val artistId: String?,
    val songCount: Int,
    val durationSeconds: Int,
    val year: Int?,
    val coverArt: String?,
    val starred: Boolean,
    val seenAt: Long = 0,
)

/**
 * Something watched on YouTube, one row per view.
 *
 * Rows rather than a JSON blob because this is the table that grows without
 * limit — the desktop app learned that the expensive way, rewriting a
 * 400 KB profile file on every scroll of the Shorts reel. An insert here costs
 * the same whether the history holds ten items or ten thousand.
 *
 * [isShort] is what keeps the two histories apart. Recommendations for the
 * Watch tab are built from videos only, deliberately: ten minutes of
 * doomscrolling should not rewrite what the app thinks you want to watch.
 */
@Entity(
    tableName = "watch_history",
    indices = [
        Index("videoId"),
        Index("isShort", "watchedAt"),
        Index("channelId"),
        // `recentAny` reads both forms at once to seed the shorts feed, so it
        // cannot use the composite index above.
        Index("watchedAt"),
    ],
)
data class WatchEntity(
    @PrimaryKey(autoGenerate = true) val rowId: Long = 0,
    val videoId: String,
    val title: String,
    val channel: String,
    val channelId: String?,
    val isShort: Boolean,
    val watchedAt: Long,
    /** Fraction of the video actually watched, 0..1. */
    val completion: Float = 0f,
)

/** A liked or disliked video. Explicit opinions outrank inferred ones. */
@Entity(tableName = "opinions")
data class OpinionEntity(
    @PrimaryKey val videoId: String,
    val title: String,
    val channel: String,
    val channelId: String?,
    val liked: Boolean,
    val at: Long,
)

/** A followed channel. Muted keeps the follow but drops it out of the feed. */
@Entity(tableName = "channels")
data class ChannelEntity(
    @PrimaryKey val id: String,
    val name: String,
    val url: String,
    val avatar: String?,
    val muted: Boolean = false,
    val followedAt: Long = 0,
)

/**
 * A feed item kept between launches.
 *
 * The desktop app looked broken for weeks because it rebuilt the feed on every
 * start and showed nothing until the network answered. Persisting it means a
 * cold start draws immediately and refreshes underneath.
 */
@Entity(tableName = "feed", indices = [Index("isShort", "rank")])
data class FeedEntity(
    @PrimaryKey val videoId: String,
    val title: String,
    val channel: String,
    val channelId: String?,
    val thumbnail: String?,
    val durationSeconds: Long,
    val viewCount: Long,
    val uploaded: String?,
    /** Why this is in the feed, shown under the title. */
    val reason: String,
    val score: Double,
    val rank: Int,
    val isShort: Boolean,
    val builtAt: Long,
)
