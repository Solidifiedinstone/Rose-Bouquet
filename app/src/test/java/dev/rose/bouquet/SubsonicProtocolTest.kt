package dev.rose.bouquet

import dev.rose.bouquet.data.Server
import dev.rose.bouquet.data.SubsonicClient
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * The client against servers that are not ours.
 *
 * Everything here was written against the same two facts: Navidrome, Airsonic,
 * Gonic and original Subsonic all speak this protocol slightly differently, and
 * the app had only ever been pointed at the desktop app's own server. Both bugs
 * these cover are invisible against that one server and immediate against the
 * others.
 *
 * The transport is an interceptor rather than a real socket, so a test can be
 * a server that is unreachable, or one that answers 404, without either being
 * true of the machine running the tests.
 */
class SubsonicProtocolTest {

    private val server = Server(id = "s", name = "", url = "http://nas.local:4533", username = "u", password = "p")

    /** A client whose every request gets [body] back with status [status]. */
    private fun answering(body: String, status: Int = 200) = SubsonicClient(
        OkHttpClient.Builder().addInterceptor { chain ->
            Response.Builder()
                .request(chain.request())
                .protocol(Protocol.HTTP_1_1)
                .code(status)
                .message("canned")
                .body(body.toByteArray().toResponseBody("application/json".toMediaType()))
                .build()
        }.build()
    )

    /** A client whose every request fails the way an absent server does. */
    private fun unreachable() = SubsonicClient(
        OkHttpClient.Builder().addInterceptor(Interceptor {
            throw IOException("Failed to connect to /192.168.50.27:4533")
        }).build()
    )

    private fun ok(inner: String) = """{"subsonic-response":{"status":"ok","version":"1.16.1",$inner}}"""

    private fun failed(code: Int, message: String) =
        """{"subsonic-response":{"status":"failed","version":"1.16.1",
           "error":{"code":$code,"message":"$message"}}}"""

    // ── A lone row arrives as an object, not a list ────────────────

    @Test
    fun `an album with one track is not empty`() {
        // Jackson-based servers — original Subsonic, Airsonic, Ampache — unwrap
        // a single-element list. Read as an array this threw, and every
        // one-track album in the library looked empty.
        val client = answering(ok("""
            "album":{"id":"a1","name":"Single","artist":"Someone","songCount":1,
                     "song":{"id":"s1","title":"Only One","artist":"Someone","album":"Single"}}
        """))
        val (album, songs) = runBlocking { client.album(server, "a1") }!!
        assertEquals("Single", album.name)
        assertEquals(listOf("Only One"), songs.map { it.title })
    }

    @Test
    fun `a playlist with one entry is not empty`() {
        val client = answering(ok("""
            "playlist":{"id":"p1","name":"One",
                        "entry":{"id":"s1","title":"Only One","artist":"A","album":"B"}}
        """))
        assertEquals(1, runBlocking { client.playlistSongs(server, "p1") }.size)
    }

    @Test
    fun `a library with one artist in one letter is not empty`() {
        val client = answering(ok("""
            "artists":{"index":{"name":"S","artist":{"id":"ar1","name":"Someone","albumCount":1}}}
        """))
        assertEquals(listOf("Someone"), runBlocking { client.artists(server) }.map { it.name })
    }

    @Test
    fun `lists still parse as lists`() {
        val client = answering(ok("""
            "playlist":{"id":"p1","name":"Two","entry":[
                {"id":"s1","title":"One","artist":"A","album":"B"},
                {"id":"s2","title":"Two","artist":"A","album":"B"}]}
        """))
        assertEquals(2, runBlocking { client.playlistSongs(server, "p1") }.size)
    }

    // ── A failure is a failure, not an empty library ───────────────

    @Test
    fun `an unreachable server is an error and not an empty library`() {
        // The bug this covers: every transport failure carried Subsonic's code
        // 0, which the client also used for "the server does not implement
        // that" — so a phone off the home network showed an empty library with
        // nothing saying why, and the downloads sitting on the device looked
        // lost.
        val error = runBlocking {
            runCatching { unreachable().albums(server) }.exceptionOrNull()
        }
        assertTrue("expected a ServerException, got $error", error is SubsonicClient.ServerException)
        assertTrue("the message should suggest the network", "network" in error!!.message!!)
    }

    @Test
    fun `a server error is an error`() {
        val client = answering(failed(50, "User is not authorized"), status = 200)
        val error = runBlocking { runCatching { client.albums(server) }.exceptionOrNull() }
        assertEquals(50, (error as SubsonicClient.ServerException).code)
    }

    @Test
    fun `a 500 is an error`() {
        val client = answering("<html>Internal Server Error</html>", status = 500)
        assertTrue(runBlocking {
            runCatching { client.albums(server) }.exceptionOrNull()
        } is SubsonicClient.ServerException)
    }

    @Test
    fun `a reply that is not Subsonic is an error`() {
        // A router's captive portal, or the wrong port entirely.
        val client = answering("""{"hello":"world"}""")
        assertTrue(runBlocking {
            runCatching { client.artists(server) }.exceptionOrNull()
        } is SubsonicClient.ServerException)
    }

    // ── A method the server does not have is still empty ───────────

    @Test
    fun `a method the server does not implement is empty rather than an error`() {
        val client = answering(failed(0, "Method not implemented"))
        assertEquals(emptyList<Any>(), runBlocking { client.starredSongs(server) })
    }

    @Test
    fun `a 404 is empty rather than an error`() {
        // Navidrome and gonic answer 404 for endpoints they do not have.
        val client = answering("not found", status = 404)
        assertEquals(emptyList<Any>(), runBlocking { client.starredSongs(server) })
    }

    @Test
    fun `data that is not there is empty rather than an error`() {
        val client = answering(failed(70, "Album not found"))
        assertEquals(null, runBlocking { client.album(server, "nope") })
    }
}
