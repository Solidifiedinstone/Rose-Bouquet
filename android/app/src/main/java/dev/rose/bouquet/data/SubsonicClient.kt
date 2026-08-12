package dev.rose.bouquet.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.security.MessageDigest
import kotlin.random.Random

/**
 * Talks to a Subsonic server — which is what the Rose Bouquet desktop app serves.
 *
 * Speaking Subsonic rather than a private protocol means this app also works
 * against Navidrome, Airsonic, Gonic or anything else that implements it, and
 * that the desktop server is useful to people who never install this app. Two
 * implementations of a shared protocol beats two ends of a private one.
 *
 * Authentication is Subsonic's salted token: `md5(password + salt)`, with a
 * fresh salt per request. The protocol is old and md5 is not a defensible
 * choice today, but a client does not get to pick — and both ends of this are
 * meant for a home network.
 */
class SubsonicClient(
    private val http: OkHttpClient = OkHttpClient(),
    private val json: Json = Json { ignoreUnknownKeys = true },
) {

    data class Server(
        val url: String,
        val username: String,
        val password: String,
    )

    data class Song(
        val id: String,
        val title: String,
        val artist: String,
        val album: String,
        val durationSeconds: Int,
        val coverArt: String?,
    )

    data class Album(
        val id: String,
        val name: String,
        val artist: String,
        val songCount: Int,
        val coverArt: String?,
    )

    class ServerException(message: String) : Exception(message)

    // ── Requests ──────────────────────────────────────────────────

    private fun endpoint(server: Server, method: String, params: Map<String, String>): HttpUrl {
        val base = server.url.trimEnd('/').toHttpUrlOrNull()
            ?: throw ServerException("That server address is not a URL")

        val salt = Random.nextLong().toString(16)
        val token = md5(server.password + salt)

        val builder = base.newBuilder()
            .addPathSegment("rest")
            .addPathSegment("$method.view")
            .addQueryParameter("u", server.username)
            .addQueryParameter("t", token)
            .addQueryParameter("s", salt)
            .addQueryParameter("v", API_VERSION)
            .addQueryParameter("c", CLIENT_NAME)
            .addQueryParameter("f", "json")

        params.forEach { (key, value) -> builder.addQueryParameter(key, value) }
        return builder.build()
    }

    private fun call(server: Server, method: String, params: Map<String, String> = emptyMap()) =
        runCatching {
            val request = Request.Builder().url(endpoint(server, method, params)).build()

            http.newCall(request).execute().use { response ->
                if (!response.isSuccessful) throw ServerException("Server said ${response.code}")

                val body = response.body?.string().orEmpty()
                val payload = json.parseToJsonElement(body).jsonObject["subsonic-response"]?.jsonObject
                    ?: throw ServerException("That does not look like a Subsonic server")

                val status = payload["status"]?.jsonPrimitive?.content
                if (status != "ok") {
                    val message = payload["error"]?.jsonObject?.get("message")?.jsonPrimitive?.content
                    throw ServerException(message ?: "The server refused the request")
                }
                payload
            }
        }

    /** Check the address and credentials before saving them. */
    suspend fun ping(server: Server): Result<Unit> = call(server, "ping").map { }

    suspend fun albums(server: Server, size: Int = 200): Result<List<Album>> =
        call(server, "getAlbumList2", mapOf("type" to "alphabeticalByName", "size" to "$size"))
            .map { payload ->
                payload["albumList2"]?.jsonObject?.get("album")?.jsonArray.orEmpty().map { entry ->
                    val row = entry.jsonObject
                    Album(
                        id = row["id"]?.jsonPrimitive?.content.orEmpty(),
                        name = row["name"]?.jsonPrimitive?.content.orEmpty(),
                        artist = row["artist"]?.jsonPrimitive?.content.orEmpty(),
                        songCount = row["songCount"]?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
                        coverArt = row["coverArt"]?.jsonPrimitive?.content,
                    )
                }
            }

    suspend fun albumSongs(server: Server, albumId: String): Result<List<Song>> =
        call(server, "getAlbum", mapOf("id" to albumId)).map { payload ->
            payload["album"]?.jsonObject?.get("song")?.jsonArray.orEmpty().map { it.toSong() }
        }

    suspend fun search(server: Server, query: String): Result<List<Song>> =
        call(server, "search3", mapOf("query" to query, "songCount" to "100")).map { payload ->
            payload["searchResult3"]?.jsonObject?.get("song")?.jsonArray.orEmpty().map { it.toSong() }
        }

    suspend fun random(server: Server, size: Int = 50): Result<List<Song>> =
        call(server, "getRandomSongs", mapOf("size" to "$size")).map { payload ->
            payload["randomSongs"]?.jsonObject?.get("song")?.jsonArray.orEmpty().map { it.toSong() }
        }

    // ── URLs the player and the image loader use directly ─────────

    fun streamUrl(server: Server, songId: String): String =
        endpoint(server, "stream", mapOf("id" to songId)).toString()

    fun coverUrl(server: Server, coverArt: String?, size: Int = 512): String? =
        coverArt?.let {
            endpoint(server, "getCoverArt", mapOf("id" to it, "size" to "$size")).toString()
        }

    private fun kotlinx.serialization.json.JsonElement.toSong(): Song {
        val row = jsonObject
        return Song(
            id = row["id"]?.jsonPrimitive?.content.orEmpty(),
            title = row["title"]?.jsonPrimitive?.content.orEmpty(),
            artist = row["artist"]?.jsonPrimitive?.content.orEmpty(),
            album = row["album"]?.jsonPrimitive?.content.orEmpty(),
            durationSeconds = row["duration"]?.jsonPrimitive?.content?.toIntOrNull() ?: 0,
            coverArt = row["coverArt"]?.jsonPrimitive?.content,
        )
    }

    private fun md5(text: String): String =
        MessageDigest.getInstance("MD5")
            .digest(text.toByteArray())
            .joinToString("") { "%02x".format(it) }

    companion object {
        const val API_VERSION = "1.16.1"
        const val CLIENT_NAME = "rose-bouquet-android"
    }
}
