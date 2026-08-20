package dev.rose.bouquet.data

import android.content.Context
import dev.rose.bouquet.data.db.AlbumEntity
import dev.rose.bouquet.data.db.RoseDatabase
import dev.rose.bouquet.data.db.SongEntity
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flowOf

/**
 * The library, from wherever it is currently coming from.
 *
 * Screens ask this rather than the client, so no screen has to know whether an
 * answer came off the network or out of the cache — which is what lets the app
 * open and be useful on a train.
 *
 * The shape is deliberate: **the cache is the source of truth for what is
 * shown, and the network only ever refreshes it.** A screen collects a Flow
 * from Room and draws whatever is there; a refresh writes to Room and the
 * screen updates itself. Nothing waits on a request to render, so a slow or
 * absent server costs freshness rather than a blank screen.
 */
class MusicRepository(
    context: Context,
    private val client: SubsonicClient = SubsonicClient(),
) {
    private val database = RoseDatabase.get(context)
    private val music = database.music()

    /**
     * Cover URLs already built, so the same art keeps the same URL.
     *
     * Bounded. Held forever, a large library and two or three requested sizes
     * is tens of thousands of entries that are never released and never
     * revisited once the user has scrolled past them — small individually and
     * unbounded together, which is the shape of a leak rather than a cache.
     */
    private val coverUrls = object : LinkedHashMap<String, String>(256, 0.75f, true) {
        override fun removeEldestEntry(eldest: Map.Entry<String, String>) = size > COVER_CACHE
    }

    // ── Reading ───────────────────────────────────────────────────

    fun songs(server: Server?): Flow<List<SongEntity>> =
        server?.let { music.songs(it.id) } ?: flowOf(emptyList())

    fun albums(server: Server?): Flow<List<AlbumEntity>> =
        server?.let { music.albums(it.id) } ?: flowOf(emptyList())

    fun albumSongs(server: Server?, albumId: String): Flow<List<SongEntity>> =
        server?.let { music.albumSongs(it.id, albumId) } ?: flowOf(emptyList())

    fun downloaded(server: Server?): Flow<List<SongEntity>> =
        server?.let { music.downloaded(it.id) } ?: flowOf(emptyList())

    /** Everything downloaded, across every server — what plays with no signal. */
    suspend fun allDownloaded(): List<SongEntity> = music.allDownloaded()

    suspend fun song(server: Server, id: String): SongEntity? = music.song(server.id, id)

    // ── Refreshing ────────────────────────────────────────────────

    /**
     * Pull the album list, and the songs of each album, into the cache.
     *
     * Albums first and songs per album, rather than one call for everything,
     * because Subsonic has no "give me every song" method — and paging albums
     * means a large library fills in visibly instead of hanging on one enormous
     * request.
     *
     * Returns the number of albums seen, or throws what the server said.
     */
    suspend fun refresh(server: Server, onProgress: (Int, Int) -> Unit = { _, _ -> }): Int {
        val stamp = System.currentTimeMillis()

        val albums = buildList {
            var offset = 0
            while (true) {
                val page = client.albums(server, size = PAGE, offset = offset)
                addAll(page)
                if (page.size < PAGE) break
                offset += PAGE
                // A server that ignores `offset` would otherwise hand back the
                // same page forever. Nothing legitimate has this many albums
                // in one library, so the cap costs nothing real.
                if (offset > MAX_ALBUMS) break
            }
        }

        music.replaceAlbums(server.id, albums.map { it.toEntity(server.id, stamp) }, stamp)

        // Which songs we hold the bytes for. Room's REPLACE is a delete and an
        // insert, so writing a refreshed row would otherwise reset `downloaded`
        // to its default and quietly disown every downloaded file — the library
        // would still play them, and the Downloads screen would show nothing.
        val alreadyDownloaded = music.allDownloaded()
            .filter { it.serverId == server.id }
            .mapTo(mutableSetOf()) { it.id }

        albums.forEachIndexed { index, album ->
            val songs = client.album(server, album.id)?.second.orEmpty()
            if (songs.isNotEmpty()) {
                music.putSongs(songs.map {
                    it.toEntity(server.id, stamp).copy(downloaded = it.id in alreadyDownloaded)
                })
            }
            onProgress(index + 1, albums.size)
        }

        music.pruneSongs(server.id, stamp)
        return albums.size
    }

    /**
     * Search, cache first and server second.
     *
     * The local hit is returned immediately so typing feels instant and works
     * with no signal; the server result replaces it when it lands, because the
     * server knows about music this phone has never listed.
     */
    suspend fun searchLocal(server: Server, query: String): List<SongEntity> =
        music.searchSongs(server.id, query)

    suspend fun searchRemote(server: Server, query: String): SubsonicClient.SearchResults =
        client.search(server, query)

    suspend fun playlists(server: Server) = client.playlists(server)

    suspend fun playlistSongs(server: Server, playlistId: String) =
        client.playlistSongs(server, playlistId).map { it.toEntity(server.id, System.currentTimeMillis()) }

    suspend fun artists(server: Server) = client.artists(server)

    suspend fun artistAlbums(server: Server, artistId: String) = client.artistAlbums(server, artistId)

    suspend fun randomSongs(server: Server, size: Int = 50) =
        client.randomSongs(server, size).map { it.toEntity(server.id, System.currentTimeMillis()) }

    // ── Acting ────────────────────────────────────────────────────

    suspend fun setStarred(server: Server, songId: String, starred: Boolean) {
        music.setStarred(server.id, songId, starred)
        client.setStarred(server, songId, starred)
    }

    suspend fun scrobble(server: Server, songId: String) = client.scrobble(server, songId)

    suspend fun markDownloaded(server: Server, songId: String, downloaded: Boolean) =
        music.setDownloaded(server.id, songId, downloaded)

    // ── URLs ──────────────────────────────────────────────────────

    fun streamUrl(server: Server, songId: String, maxBitrateKbps: Int = 0) =
        client.streamUrl(server, songId, maxBitrateKbps)
    fun downloadUrl(server: Server, songId: String) = client.downloadUrl(server, songId)
    /**
     * A stable URL for one piece of cover art.
     *
     * Memoised, and that is the whole point rather than an optimisation.
     * Subsonic authenticates with a token salted per request, so asking for the
     * same cover twice produces two different URLs — and this is called from
     * composition, so every recomposition handed the image loader a URL it had
     * never seen, which cancelled the in-flight request and started another.
     * With a list scrolling or a position ticking twice a second, the art never
     * finished loading and the screen simply stayed blank.
     */
    fun coverUrl(server: Server, coverArt: String?, size: Int = 512): String? {
        val art = coverArt?.takeIf { it.isNotBlank() } ?: return null
        val key = "${server.id}|$art|$size"
        // Synchronised because LinkedHashMap in access order mutates on read,
        // and this is called from composition on the main thread while a
        // library refresh may be filling it from an IO thread.
        synchronized(coverUrls) {
            coverUrls[key]?.let { return it }
            val built = client.coverUrl(server, art, size) ?: return null
            coverUrls[key] = built
            return built
        }
    }

    companion object {
        private const val PAGE = 100
        private const val MAX_ALBUMS = 20_000

        /** Enough for several screens of art either side of where you are. */
        private const val COVER_CACHE = 600
    }
}

// ── Converting between the wire and the cache ─────────────────────

fun SubsonicClient.Song.toEntity(serverId: String, stamp: Long) = SongEntity(
    serverId = serverId, id = id, title = title, artist = artist, album = album,
    albumId = albumId, durationSeconds = durationSeconds, track = track, year = year,
    coverArt = coverArt, suffix = suffix, sizeBytes = sizeBytes, starred = starred,
    seenAt = stamp,
)

fun SubsonicClient.Album.toEntity(serverId: String, stamp: Long) = AlbumEntity(
    serverId = serverId, id = id, name = name, artist = artist, artistId = artistId,
    songCount = songCount, durationSeconds = durationSeconds, year = year,
    coverArt = coverArt, starred = starred, seenAt = stamp,
)
