package dev.rose.bouquet.data.db

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow

@Dao
interface MusicDao {

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putSongs(songs: List<SongEntity>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putAlbums(albums: List<AlbumEntity>)

    @Query("SELECT * FROM songs WHERE serverId = :serverId ORDER BY artist, album, track, title")
    fun songs(serverId: String): Flow<List<SongEntity>>

    @Query("SELECT * FROM songs WHERE serverId = :serverId AND albumId = :albumId ORDER BY track, title")
    fun albumSongs(serverId: String, albumId: String): Flow<List<SongEntity>>

    @Query("SELECT * FROM albums WHERE serverId = :serverId ORDER BY name")
    fun albums(serverId: String): Flow<List<AlbumEntity>>

    @Query("SELECT * FROM songs WHERE serverId = :serverId AND downloaded = 1 ORDER BY artist, album, track")
    fun downloaded(serverId: String): Flow<List<SongEntity>>

    @Query("SELECT * FROM songs WHERE downloaded = 1")
    suspend fun allDownloaded(): List<SongEntity>

    @Query("SELECT * FROM songs WHERE serverId = :serverId AND id = :id")
    suspend fun song(serverId: String, id: String): SongEntity?

    @Query("UPDATE songs SET downloaded = :downloaded WHERE serverId = :serverId AND id = :id")
    suspend fun setDownloaded(serverId: String, id: String, downloaded: Boolean)

    @Query("UPDATE songs SET starred = :starred WHERE serverId = :serverId AND id = :id")
    suspend fun setStarred(serverId: String, id: String, starred: Boolean)

    /**
     * Search what is already on the phone.
     *
     * Used offline, and used to fill the screen the instant somebody types
     * while the server's own search is still in flight.
     */
    @Query("""
        SELECT * FROM songs
        WHERE serverId = :serverId
          AND (title LIKE '%' || :query || '%'
            OR artist LIKE '%' || :query || '%'
            OR album LIKE '%' || :query || '%')
        ORDER BY artist, album, track
        LIMIT :limit
    """)
    suspend fun searchSongs(serverId: String, query: String, limit: Int = 100): List<SongEntity>

    /**
     * Drop rows the last refresh did not see, without touching downloads.
     *
     * A song deleted on the server should leave the library — but if the bytes
     * are on this phone it is still playable, and removing it would delete
     * music the user deliberately kept.
     */
    @Query("DELETE FROM songs WHERE serverId = :serverId AND seenAt < :before AND downloaded = 0")
    suspend fun pruneSongs(serverId: String, before: Long)

    @Query("DELETE FROM albums WHERE serverId = :serverId AND seenAt < :before")
    suspend fun pruneAlbums(serverId: String, before: Long)

    @Transaction
    suspend fun replaceAlbums(serverId: String, albums: List<AlbumEntity>, stamp: Long) {
        putAlbums(albums)
        pruneAlbums(serverId, stamp)
    }
}

@Dao
interface YouTubeDao {

    // ── History ───────────────────────────────────────────────────

    @Insert
    suspend fun watched(row: WatchEntity)

    /**
     * Recent views, videos and shorts kept apart.
     *
     * The separation is the feature: [WatchEntity.isShort] is what stops a
     * shorts binge from rewriting the Watch tab's idea of your taste.
     */
    @Query("SELECT * FROM watch_history WHERE isShort = :shorts ORDER BY watchedAt DESC LIMIT :limit")
    suspend fun recent(shorts: Boolean, limit: Int = 400): List<WatchEntity>

    /** Every id ever watched, for excluding things already seen. */
    @Query("SELECT DISTINCT videoId FROM watch_history")
    suspend fun watchedIds(): List<String>

    /** How many times each channel has been watched — the taste signal that matters most. */
    @Query("""
        SELECT channelId AS id, COUNT(*) AS plays FROM watch_history
        WHERE channelId IS NOT NULL AND isShort = :shorts
        GROUP BY channelId ORDER BY plays DESC LIMIT :limit
    """)
    suspend fun topChannels(shorts: Boolean, limit: Int = 40): List<ChannelPlays>

    @Query("SELECT COUNT(*) FROM watch_history WHERE isShort = :shorts")
    suspend fun historyCount(shorts: Boolean): Int

    @Query("DELETE FROM watch_history")
    suspend fun clearHistory()

    // ── Opinions ──────────────────────────────────────────────────

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun setOpinion(row: OpinionEntity)

    @Query("DELETE FROM opinions WHERE videoId = :videoId")
    suspend fun clearOpinion(videoId: String)

    @Query("SELECT * FROM opinions WHERE liked = :liked ORDER BY at DESC LIMIT :limit")
    suspend fun opinions(liked: Boolean, limit: Int = 200): List<OpinionEntity>

    @Query("SELECT videoId FROM opinions WHERE liked = 0")
    suspend fun dislikedIds(): List<String>

    @Query("SELECT * FROM opinions WHERE videoId = :videoId")
    fun opinion(videoId: String): Flow<OpinionEntity?>

    // ── Channels ──────────────────────────────────────────────────

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun follow(channel: ChannelEntity)

    @Query("DELETE FROM channels WHERE id = :id")
    suspend fun unfollow(id: String)

    @Query("SELECT * FROM channels ORDER BY name COLLATE NOCASE")
    fun channels(): Flow<List<ChannelEntity>>

    @Query("SELECT * FROM channels WHERE muted = 0")
    suspend fun activeChannels(): List<ChannelEntity>

    @Query("UPDATE channels SET muted = :muted WHERE id = :id")
    suspend fun setMuted(id: String, muted: Boolean)

    @Query("SELECT EXISTS(SELECT 1 FROM channels WHERE id = :id)")
    fun follows(id: String): Flow<Boolean>

    // ── Feed ──────────────────────────────────────────────────────

    @Query("SELECT * FROM feed WHERE isShort = :shorts ORDER BY rank")
    fun feed(shorts: Boolean): Flow<List<FeedEntity>>

    @Query("SELECT COUNT(*) FROM feed WHERE isShort = :shorts")
    suspend fun feedSize(shorts: Boolean): Int

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun putFeed(items: List<FeedEntity>)

    @Query("DELETE FROM feed WHERE isShort = :shorts")
    suspend fun clearFeed(shorts: Boolean)

    @Query("DELETE FROM feed WHERE videoId = :videoId")
    suspend fun dropFromFeed(videoId: String)

    @Transaction
    suspend fun replaceFeed(shorts: Boolean, items: List<FeedEntity>) {
        clearFeed(shorts)
        putFeed(items)
    }
}

/** How many times one channel appears in the history. */
data class ChannelPlays(val id: String, val plays: Int)
