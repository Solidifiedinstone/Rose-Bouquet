package dev.rose.bouquet.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
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
     *
     * [unsupported] is the narrow case of a server that answered and said it
     * does not implement the method. It is a separate flag rather than a code
     * because Subsonic spends code 0 on *both* "no such method" and "something
     * went wrong", and only one of those two may be quietly turned into an
     * empty screen — see [callOptional].
     */
    class ServerException(
        message: String,
        val code: Int = 0,
        val unsupported: Boolean = false,
    ) : Exception(message)

    // ── Making a request ──────────────────────────────────────────

    /**
     * Ask a Rose Bouquet server for its YouTube session.
     *
     * Rose's own endpoint, not Subsonic's, so any other server answers 404 and
     * this comes back null — which is the right answer for Navidrome, and not
     * an error worth showing anybody.
     *
     * Returns the session as a `Cookie:` header, an empty string when the
     * desktop has one to give but sharing is switched off, and null when there
     * is no Rose server at the other end.
     */
    suspend fun youtubeSession(server: Server): String? = withContext(Dispatchers.IO) {
        val url = roseEndpoint(server, "youtube-session") ?: return@withContext null
        val request = okhttp3.Request.Builder().url(url).build()
        runCatching {
            http.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (body.isBlank()) return@use null
                val json = runCatching { JSONObject(body) }.getOrNull() ?: return@use null
                if (!json.optBoolean("shared", false)) "" else json.optString("cookie", "")
            }
        }.getOrNull()
    }

    /** The same auth as everything else, on Rose's own `/api/` path. */
    private fun roseEndpoint(server: Server, action: String): HttpUrl? {
        val base = normalise(server.url) ?: return null
        val builder = base.newBuilder()
            .addPathSegment("api")
            .addPathSegment(action)
            .addQueryParameter("u", server.username)
            .addQueryParameter("v", API_VERSION)
            .addQueryParameter("c", CLIENT_NAME)
            .addQueryParameter("f", "json")

        if (server.plaintextPassword) {
            builder.addQueryParameter("p", server.password)
        } else {
            val salt = Random.nextLong().toString(16)
            builder.addQueryParameter("t", md5(server.password + salt))
            builder.addQueryParameter("s", salt)
        }
        return builder.build()
    }

    private fun endpoint(server: Server, method: String, params: Map<String, String>): HttpUrl {
        val base = normalise(server.url)
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
     * Turn what somebody typed into a URL.
     *
     * "192.168.1.10:4533" is what a person writes down and reads back off a
     * screen; it is not a URL, and rejecting it as one teaches nothing. A
     * missing scheme becomes `http://`, which is what a server on a home
     * network almost always is — and if it is actually HTTPS, typing the
     * scheme is the one case where being explicit is no hardship.
     */
    private fun normalise(raw: String): HttpUrl? {
        val trimmed = raw.trim().trimEnd('/')
        if (trimmed.isBlank()) return null
        val withScheme =
            if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) trimmed
            else "http://$trimmed"
        return withScheme.toHttpUrlOrNull()
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
                    // A server without a method usually answers 404 or 501
                    // rather than a Subsonic error body — Navidrome and gonic
                    // both do. That is a missing feature, not a broken server.
                    throw ServerException(
                        "The server answered ${response.code}",
                        unsupported = response.code == 404 || response.code == 501,
                    )
                }
                response.body?.string().orEmpty()
            }
        } catch (e: IOException) {
            throw ServerException(explain(e), 0)
        }

        val payload = runCatching {
            json.parseToJsonElement(body).jsonObject["subsonic-response"]?.jsonObject
        }.getOrNull() ?: throw ServerException("That does not look like a Subsonic server")

        if (payload["status"]?.jsonPrimitive?.content != "ok") {
            val error = payload["error"]?.jsonObject
            val code = error?.get("code")?.jsonPrimitive?.content?.toIntOrNull() ?: 0
            throw ServerException(
                error?.get("message")?.jsonPrimitive?.content ?: "The server refused the request",
                code,
                unsupported = code == UNSUPPORTED_METHOD || code == NOT_FOUND,
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
     *
     * Which is why this tests [ServerException.unsupported] and not the code.
     * A failure that never reached a server also carries code 0, so testing the
     * code meant a phone off the home network, an HTTPS certificate the phone
     * would not trust, and a server returning 500 all arrived at the screen as
     * an empty library — no error, nothing to investigate, and the music that
     * was downloaded and sitting right there looking like it had been lost.
     */
    private suspend fun callOptional(
        server: Server,
        method: String,
        params: Map<String, String> = emptyMap(),
    ): JsonObject? = try {
        call(server, method, params)
    } catch (e: ServerException) {
        if (e.unsupported) null else throw e
    }

    /**
     * What went wrong, in words that suggest what to do.
     *
     * OkHttp's own messages are accurate and unhelpful: "Failed to connect to
     * /192.168.1.10:4533" does not tell somebody their phone is on mobile data
     * and the server is on their home wifi, which is what it usually means.
     */
    private fun explain(e: IOException): String {
        val message = e.message.orEmpty()
        return when {
            "CLEARTEXT" in message ->
                "This build will not use plain HTTP. Update the app, or use an https:// address."
            "Failed to connect" in message || "connect timed out" in message.lowercase() ->
                "Could not reach that address. Check the port, and that this phone is on the " +
                    "same network as the server."
            "Unable to resolve host" in message ->
                "That hostname does not resolve. On a home network an IP address is safer " +
                    "than a name."
            "trust anchor" in message || "CertPath" in message ->
                "That server's HTTPS certificate is not trusted. A self-signed certificate " +
                    "has to be installed on the phone first, or use http:// instead."
            else -> message.ifBlank { "Could not reach the server" }
        }
    }

    // ── Methods ───────────────────────────────────────────────────

    /** Check the address and credentials before saving them. */
    suspend fun ping(server: Server) = call(server, "ping").let { }

    suspend fun artists(server: Server): List<Artist> {
        val payload = callOptional(server, "getArtists") ?: return emptyList()
        // Artists arrive bucketed under alphabetical index letters.
        return payload.obj("artists").rows("index")
            .flatMap { (it as? JsonObject).rows("artist") }
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

        return payload.obj("albumList2").rows("album").map { it.toAlbum() }
    }

    suspend fun album(server: Server, albumId: String): Pair<Album, List<Song>>? {
        val payload = callOptional(server, "getAlbum", mapOf("id" to albumId)) ?: return null
        val row = payload.obj("album") ?: return null
        return row.toAlbum() to row.rows("song").map { it.toSong() }
    }

    suspend fun artistAlbums(server: Server, artistId: String): List<Album> {
        val payload = callOptional(server, "getArtist", mapOf("id" to artistId)) ?: return emptyList()
        return payload.obj("artist").rows("album").map { it.toAlbum() }
    }

    suspend fun search(server: Server, query: String, limit: Int = 100): SearchResults {
        val payload = callOptional(
            server, "search3",
            mapOf(
                "query" to query,
                "songCount" to "$limit", "albumCount" to "$limit", "artistCount" to "$limit",
            ),
        ) ?: return SearchResults(emptyList(), emptyList(), emptyList())

        val result = payload.obj("searchResult3")
        return SearchResults(
            songs = result.rows("song").map { it.toSong() },
            albums = result.rows("album").map { it.toAlbum() },
            artists = result.rows("artist").map { entry ->
                val row = entry.jsonObject
                Artist(row.str("id"), row.str("name"), row.int("albumCount") ?: 0, row.strOrNull("coverArt"))
            },
        )
    }

    suspend fun playlists(server: Server): List<Playlist> {
        val payload = callOptional(server, "getPlaylists") ?: return emptyList()
        return payload.obj("playlists").rows("playlist").map { entry ->
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
        return payload.obj("playlist").rows("entry").map { it.toSong() }
    }

    suspend fun randomSongs(server: Server, size: Int = 50): List<Song> {
        val payload = callOptional(server, "getRandomSongs", mapOf("size" to "$size"))
            ?: return emptyList()
        return payload.obj("randomSongs").rows("song").map { it.toSong() }
    }

    suspend fun starredSongs(server: Server): List<Song> {
        val payload = callOptional(server, "getStarred2") ?: return emptyList()
        return payload.obj("starred2").rows("song").map { it.toSong() }
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

    /**
     * A URL to stream from.
     *
     * [maxBitrateKbps] asks the server to transcode down, which is the whole
     * point on mobile data: a FLAC rip is perhaps 900 kbps and nobody wants to
     * spend that on a phone. 0 means "send what you have" — and a server that
     * cannot transcode ignores the parameter, which is fine, because then
     * there was never a cheaper version to be had.
     */
    fun streamUrl(server: Server, songId: String, maxBitrateKbps: Int = 0): String {
        val params = buildMap {
            put("id", songId)
            if (maxBitrateKbps > 0) {
                put("maxBitRate", "$maxBitrateKbps")
                // Naming a container as well, because several servers only
                // transcode when they know what to transcode *to*.
                put("format", "mp3")
            }
        }
        return endpoint(server, "stream", params).toString()
    }

    fun downloadUrl(server: Server, songId: String): String =
        endpoint(server, "download", mapOf("id" to songId)).toString()

    fun coverUrl(server: Server, coverArt: String?, size: Int = 512): String? =
        coverArt?.takeIf { it.isNotBlank() }?.let {
            endpoint(server, "getCoverArt", mapOf("id" to it, "size" to "$size")).toString()
        }

    // ── Parsing ───────────────────────────────────────────────────

    /**
     * The rows under [key], whether they came as a list or as one bare object.
     *
     * Servers built on Jackson — original Subsonic, Airsonic, Ampache — unwrap
     * a single-element list when they render JSON, so an album with one track
     * sends `"song": {…}` where an album with two sends `"song": […]`. Reading
     * it as an array throws on the first shape, which turned every one-track
     * album, one-song playlist and single-artist library into an empty screen
     * on exactly the servers this client exists to talk to. Navidrome always
     * sends a list, which is why this never showed up in testing.
     */
    private fun JsonObject?.rows(key: String): List<JsonElement> =
        when (val value = this?.get(key)) {
            is JsonArray -> value
            is JsonObject -> listOf(value)
            else -> emptyList()
        }

    /** A nested object under [key], or null if it is missing or not an object. */
    private fun JsonObject?.obj(key: String): JsonObject? = this?.get(key) as? JsonObject

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
