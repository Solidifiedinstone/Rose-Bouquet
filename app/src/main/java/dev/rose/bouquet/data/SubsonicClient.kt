package dev.rose.bouquet.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import kotlin.random.Random

/**
 * Talks to a Subsonic server — which is what the Rose Bouquet desktop app
 * serves, and also what Navidrome, Airsonic, Gonic and Ampache serve.
 *
 * Speaking the shared protocol rather than a private one means this app is
 * useful to people who never run the desktop app, and the desktop server is
 * useful to people who never install this. Two implementations of an open
 * protocol beats two ends of a closed one.
 *
 * **Servers differ, and that is the normal case.** The desktop server
 * implements the subset it needs; Navidrome implements far more. Every method
 * here fails softly into an empty list rather than an error when the server
 * simply does not know the method, so a missing feature greys out one screen
 * instead of breaking the app — see [callOptional].
 */
class SubsonicClient(
    private val http: OkHttpClient = defaultHttpClient(),
    private val json: Json = Json { ignoreUnknownKeys = true },
) {

    // ── What comes back ───────────────────────────────────────────

    data class Song(
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
    )

    data class Album(
        val id: String,
        val name: String,
        val artist: String,
        val artistId: String?,
        val songCount: Int,
        val durationSeconds: Int,
        val year: Int?,
        val coverArt: String?,
        val starred: Boolean,
    )

    data class Artist(
        val id: String,
        val name: String,
        val albumCount: Int,
        val coverArt: String?,
    )

    data class Playlist(
        val id: String,
        val name: String,
        val songCount: Int,
        val durationSeconds: Int,
        val comment: String?,
        val coverArt: String?,
    )

    data class SearchResults(
        val songs: List<Song>,
        val albums: List<Album>,
        val artists: List<Artist>,
    ) {
        val isEmpty: Boolean get() = songs.isEmpty() && albums.isEmpty() && artists.isEmpty()
    }

    /**
     * A server said no, and this is what it said.
     *
     * [code] is Subsonic's own error code where there was one. 0 means the
     * failure happened before the server got a chance to have an opinion —
     * an unreachable host, a wrong address, a reply that was not Subsonic.
     */
    class ServerException(message: String, val code: Int = 0) : Exception(message)

    // ── Making a request ──────────────────────────────────────────

    private fun endpoint(server: Server, method: String, params: Map<String, String>): HttpUrl {
        val base = server.url.trim().trimEnd('/').toHttpUrlOrNull()
            ?: throw ServerException("That server address is not a URL")

        val builder = base.newBuilder()
            .addPathSegment("rest")
            .addPathSegment("$method.view")
            .addQueryParameter("u", server.username)
            .addQueryParameter("v", API_VERSION)
            .addQueryParameter("c", CLIENT_NAME)
            .addQueryParameter("f", "json")

        if (server.plaintextPassword) {
            // Some older and smaller servers never implemented the salted
            // token. Sending the password itself is worse, so it is opt-in per
            // server and the settings screen says what it costs.
            builder.addQueryParameter("p", server.password)
        } else {
            val salt = Random.nextLong().toString(16)
            builder.addQueryParameter("t", md5(server.password + salt))
            builder.addQueryParameter("s", salt)
        }

        params.forEach { (key, value) -> builder.addQueryParameter(key, value) }
        return builder.build()
    }

    /**
     * Run a method and hand back the `subsonic-response` body.
     *
     * On the IO dispatcher because OkHttp's synchronous call blocks, and
     * blocking a coroutine on the main dispatcher is how a list turns janky
     * for reasons that never show up in the code doing the scrolling.
     */
    private suspend fun call(
        server: Server,
        method: String,
        params: Map<String, String> = emptyMap(),
    ): JsonObject = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(endpoint(server, method, params)).build()

        val body = try {
            http.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    throw ServerException("The server answered ${response.code}")
                }
                response.body?.string().orEmpty()
            }
        } catch (e: IOException) {
            throw ServerException(e.message ?: "Could not reach the server")
        }

        val payload = runCatching {
            json.parseToJsonElement(body).jsonObject["subsonic-response"]?.jsonObject
        }.getOrNull() ?: throw ServerException("That does not look like a Subsonic server")

        if (payload["status"]?.jsonPrimitive?.content != "ok") {
            val error = payload["error"]?.jsonObject
            throw ServerException(
                error?.get("message")?.jsonPrimitive?.content ?: "The server refused the request",
                error?.get("code")?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
            )
        }
        payload
    }

    /**
     * Like [call], but treats "I do not implement that" as an empty answer.
     *
     * Subsonic code 0 is what a server returns for a method it does not know —
     * the desktop app's own server answers exactly that for anything outside
     * the subset it implements, and code 70 means the thing simply is not
     * there. Neither is an error worth showing somebody: the screen should say
     * it is empty, not that something went wrong. A real failure — no network,
     * bad credentials, a 500 — still throws.
     */
    private suspend fun callOptional(
        server: Server,
        method: String,
        params: Map<String, String> = emptyMap(),
    ): JsonObject? = try {
        call(server, method, params)
    } catch (e: ServerException) {
        if (e.code == UNSUPPORTED_METHOD || e.code == NOT_FOUND) null else throw e
    }

    // ── Methods ───────────────────────────────────────────────────

    /** Check the address and credentials before saving them. */
    suspend fun ping(server: Server) = call(server, "ping").let { }

    suspend fun artists(server: Server): List<Artist> {
        val payload = callOptional(server, "getArtists") ?: return emptyList()
        // Artists arrive bucketed under alphabetical index letters.
        return payload["artists"]?.jsonObject?.get("index")?.jsonArray.orEmpty()
            .flatMap { it.jsonObject["artist"]?.jsonArray.orEmpty() }
            .map { entry ->
                val row = entry.jsonObject
                Artist(
                    id = row.str("id"),
                    name = row.str("name"),
                    albumCount = row.int("albumCount") ?: 0,
                    coverArt = row.strOrNull("coverArt"),
                )
            }
    }

    /**
     * Albums, paged.
     *
     * [type] is Subsonic's sort — `alphabeticalByName`, `newest`, `recent`,
     * `frequent`, `random`, `starred`. The desktop server ignores it and
     * returns everything, which is a fine thing for a client to be robust to.
     */
    suspend fun albums(
        server: Server,
        type: String = "alphabeticalByName",
        size: Int = 100,
        offset: Int = 0,
    ): List<Album> {
        val payload = callOptional(
            server, "getAlbumList2",
            mapOf("type" to type, "size" to "$size", "offset" to "$offset"),
        ) ?: return emptyList()

        return payload["albumList2"]?.jsonObject?.get("album")?.jsonArray.orEmpty()
            .map { it.toAlbum() }
    }

    suspend fun album(server: Server, albumId: String): Pair<Album, List<Song>>? {
        val payload = callOptional(server, "getAlbum", mapOf("id" to albumId)) ?: return null
        val row = payload["album"]?.jsonObject ?: return null
        return row.toAlbum() to row["song"]?.jsonArray.orEmpty().map { it.toSong() }
    }

    suspend fun artistAlbums(server: Server, artistId: String): List<Album> {
        val payload = callOptional(server, "getArtist", mapOf("id" to artistId)) ?: return emptyList()
        return payload["artist"]?.jsonObject?.get("album")?.jsonArray.orEmpty().map { it.toAlbum() }
    }

    suspend fun search(server: Server, query: String, limit: Int = 100): SearchResults {
        val payload = callOptional(
            server, "search3",
            mapOf(
                "query" to query,
                "songCount" to "$limit", "albumCount" to "$limit", "artistCount" to "$limit",
            ),
        ) ?: return SearchResults(emptyList(), emptyList(), emptyList())

        val result = payload["searchResult3"]?.jsonObject
        return SearchResults(
            songs = result?.get("song")?.jsonArray.orEmpty().map { it.toSong() },
            albums = result?.get("album")?.jsonArray.orEmpty().map { it.toAlbum() },
            artists = result?.get("artist")?.jsonArray.orEmpty().map { entry ->
                val row = entry.jsonObject
                Artist(row.str("id"), row.str("name"), row.int("albumCount") ?: 0, row.strOrNull("coverArt"))
            },
        )
    }

    suspend fun playlists(server: Server): List<Playlist> {
        val payload = callOptional(server, "getPlaylists") ?: return emptyList()
        return payload["playlists"]?.jsonObject?.get("playlist")?.jsonArray.orEmpty().map { entry ->
            val row = entry.jsonObject
            Playlist(
                id = row.str("id"),
                name = row.str("name"),
                songCount = row.int("songCount") ?: 0,
                durationSeconds = row.int("duration") ?: 0,
                comment = row.strOrNull("comment"),
                coverArt = row.strOrNull("coverArt"),
            )
        }
    }

    suspend fun playlistSongs(server: Server, playlistId: String): List<Song> {
        val payload = callOptional(server, "getPlaylist", mapOf("id" to playlistId)) ?: return emptyList()
        return payload["playlist"]?.jsonObject?.get("entry")?.jsonArray.orEmpty().map { it.toSong() }
    }

    suspend fun randomSongs(server: Server, size: Int = 50): List<Song> {
        val payload = callOptional(server, "getRandomSongs", mapOf("size" to "$size"))
            ?: return emptyList()
        return payload["randomSongs"]?.jsonObject?.get("song")?.jsonArray.orEmpty().map { it.toSong() }
    }

    suspend fun starredSongs(server: Server): List<Song> {
        val payload = callOptional(server, "getStarred2") ?: return emptyList()
        return payload["starred2"]?.jsonObject?.get("song")?.jsonArray.orEmpty().map { it.toSong() }
    }

    /** Star or unstar. Silently does nothing on a server without the method. */
    suspend fun setStarred(server: Server, songId: String, starred: Boolean) {
        callOptional(server, if (starred) "star" else "unstar", mapOf("id" to songId))
    }

    /**
     * Tell the server something was played.
     *
     * Best-effort by design: a server that does not scrobble, or a phone that
     * lost signal mid-song, must not turn into an error over playback that
     * already happened and went fine.
     */
    suspend fun scrobble(server: Server, songId: String, submission: Boolean = true) {
        runCatching {
            callOptional(server, "scrobble", mapOf("id" to songId, "submission" to "$submission"))
        }
    }

    // ── URLs handed to the player and the image loader ────────────

    fun streamUrl(server: Server, songId: String): String =
        endpoint(server, "stream", mapOf("id" to songId)).toString()

    fun downloadUrl(server: Server, songId: String): String =
        endpoint(server, "download", mapOf("id" to songId)).toString()

    fun coverUrl(server: Server, coverArt: String?, size: Int = 512): String? =
        coverArt?.takeIf { it.isNotBlank() }?.let {
            endpoint(server, "getCoverArt", mapOf("id" to it, "size" to "$size")).toString()
        }

    // ── Parsing ───────────────────────────────────────────────────

    private fun JsonObject.str(key: String) = this[key]?.jsonPrimitive?.content.orEmpty()
    private fun JsonObject.strOrNull(key: String) =
        this[key]?.jsonPrimitive?.content?.takeIf { it.isNotBlank() }

    private fun JsonObject.int(key: String) = this[key]?.jsonPrimitive?.content?.toIntOrNull()
    private fun JsonObject.long(key: String) = this[key]?.jsonPrimitive?.content?.toLongOrNull()
    private fun JsonObject.bool(key: String) = this[key] != null

    private fun JsonElement.toSong(): Song {
        val row = jsonObject
        return Song(
            id = row.str("id"),
            title = row.str("title"),
            artist = row.strOrNull("artist") ?: "Unknown artist",
            album = row.strOrNull("album") ?: "Unknown album",
            albumId = row.strOrNull("albumId"),
            durationSeconds = row.int("duration") ?: 0,
            track = row.int("track"),
            year = row.int("year"),
            coverArt = row.strOrNull("coverArt"),
            suffix = row.strOrNull("suffix"),
            sizeBytes = row.long("size"),
            // Subsonic marks a starred item with a timestamp and omits the key
            // otherwise, so presence is the flag.
            starred = row.bool("starred"),
        )
    }

    private fun JsonElement.toAlbum(): Album {
        val row = jsonObject
        return Album(
            id = row.str("id"),
            name = row.strOrNull("name") ?: row.strOrNull("album") ?: "Unknown album",
            artist = row.strOrNull("artist") ?: "Unknown artist",
            artistId = row.strOrNull("artistId"),
            songCount = row.int("songCount") ?: 0,
            durationSeconds = row.int("duration") ?: 0,
            year = row.int("year"),
            coverArt = row.strOrNull("coverArt"),
            starred = row.bool("starred"),
        )
    }

    private fun md5(text: String): String =
        MessageDigest.getInstance("MD5")
            .digest(text.toByteArray())
            .joinToString("") { "%02x".format(it) }

    companion object {
        const val API_VERSION = "1.16.1"
        const val CLIENT_NAME = "rose-bouquet-android"

        /** Subsonic's "the server does not implement this method". */
        private const val UNSUPPORTED_METHOD = 0

        /** Subsonic's "the requested data was not found". */
        private const val NOT_FOUND = 70

        /**
         * Timeouts short enough that a server which is not there says so.
         *
         * A phone that has wandered off the home network is the common case,
         * not the exceptional one, and OkHttp's default of no read timeout at
         * all means the library screen would spin indefinitely instead of
         * offering the downloaded music that is sitting right there.
         */
        fun defaultHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }
}
