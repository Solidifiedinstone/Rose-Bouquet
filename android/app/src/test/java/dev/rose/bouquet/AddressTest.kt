package dev.rose.bouquet

import dev.rose.bouquet.data.Server
import dev.rose.bouquet.data.SubsonicClient
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What somebody types into the address box.
 *
 * A home server is read off a router page or a terminal and typed in by hand,
 * so the input is "192.168.50.27:4533" far more often than it is a URL.
 * Rejecting that as "not a URL" is technically true and useless.
 */
class AddressTest {

    private val client = SubsonicClient()

    private fun url(address: String) = client.streamUrl(
        Server(id = "s", name = "", url = address, username = "u", password = "p"),
        "song-1",
    )

    @Test
    fun `a bare address and port gets http`() {
        assertTrue(url("192.168.50.27:4533").startsWith("http://192.168.50.27:4533/rest/"))
    }

    @Test
    fun `a bare hostname gets http`() {
        assertTrue(url("nas.local:4533").startsWith("http://nas.local:4533/rest/"))
    }

    @Test
    fun `an explicit scheme is left alone`() {
        assertTrue(url("https://music.example.com").startsWith("https://music.example.com/rest/"))
        assertTrue(url("http://10.0.0.5:4533").startsWith("http://10.0.0.5:4533/rest/"))
    }

    @Test
    fun `trailing slashes do not double up`() {
        assertTrue("//rest/" !in url("http://10.0.0.5:4533/"))
    }

    @Test
    fun `a subpath is kept`() {
        // Servers behind a reverse proxy commonly live under a path.
        assertTrue(url("example.com/music").startsWith("http://example.com/music/rest/"))
    }

    @Test(expected = SubsonicClient.ServerException::class)
    fun `an empty address is still an error`() {
        url("   ")
    }
}
