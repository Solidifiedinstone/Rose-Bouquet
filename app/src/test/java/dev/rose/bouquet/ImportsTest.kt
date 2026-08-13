package dev.rose.bouquet

import dev.rose.bouquet.data.Imports
import dev.rose.bouquet.data.Server
import dev.rose.bouquet.data.SubsonicClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Spotify page parser.
 *
 * Worth testing without a network because the failure it guards against is
 * silent: a parser that returns ids instead of titles still "works", still
 * reports a count, and then matches nothing at all when those ids are searched
 * for on YouTube.
 */
class SpotifyParserTest {

    private fun page(body: String) = """
        <html><head>
          <meta name="music:song" content="https://open.spotify.com/track/aaa"/>
          <meta name="music:song" content="https://open.spotify.com/track/bbb"/>
        </head><body>$body</body></html>
    """.trimIndent()

    @Test
    fun `titles and artists come out of the embedded data`() {
        val blob = """
            <script id="__NEXT_DATA__" type="application/json">
            {"props":{"page":{"items":[
              {"name":"Dayvan Cowboy","artists":{"items":[{"profile":{"name":"Boards of Canada"}}]}},
              {"name":"Roygbiv","artists":{"items":[{"profile":{"name":"Boards of Canada"}}]}}
            ]}}}
            </script>
        """.trimIndent()

        val tracks = Imports.parsePlaylistPage(page(blob))
        assertEquals(2, tracks.size)
        assertEquals("Dayvan Cowboy", tracks[0].title)
        assertEquals("Boards of Canada", tracks[0].artist)
    }

    @Test
    fun `a track nested anywhere is still found`() {
        // Spotify reshapes this blob regularly, so the walk is structural
        // rather than following a fixed path. This is a path they have never
        // used, and it must still work.
        val blob = """
            <script id="__NEXT_DATA__" type="application/json">
            {"a":{"b":{"c":[{"d":{"name":"Sunshine Recorder",
              "artists":[{"name":"Boards of Canada"}]}}]}}}
            </script>
        """.trimIndent()

        val tracks = Imports.parsePlaylistPage(page(blob))
        assertEquals(1, tracks.size)
        assertEquals("Sunshine Recorder", tracks[0].title)
    }

    @Test
    fun `duplicates within one playlist collapse`() {
        val blob = """
            <script id="__NEXT_DATA__" type="application/json">
            {"items":[
              {"name":"Roygbiv","artists":[{"name":"Boards of Canada"}]},
              {"name":"roygbiv","artists":[{"name":"boards of canada"}]}
            ]}
            </script>
        """.trimIndent()

        assertEquals(1, Imports.parsePlaylistPage(page(blob)).size)
    }

    @Test
    fun `without the blob it falls back to the meta tags`() {
        // Not useful for matching, but it proves the playlist exists and how
        // long it is, which is worth reporting rather than showing nothing.
        val tracks = Imports.parsePlaylistPage(page("<p>no script here</p>"))
        assertEquals(2, tracks.size)
        assertEquals("aaa", tracks[0].title)
    }

    @Test
    fun `rubbish in does not throw`() {
        assertTrue(Imports.parsePlaylistPage("").isEmpty())
        assertTrue(
            Imports.parsePlaylistPage(
                "<script id=\"__NEXT_DATA__\">{not json at all</script>"
            ).isEmpty()
        )
    }
}

/**
 * Stream URLs.
 *
 * The bitrate ceiling is the kind of parameter that is easy to add to the code
 * and never actually put on the wire.
 */
class StreamUrlTest {

    private val client = SubsonicClient()
    private val server = Server(
        id = "s", name = "Home", url = "http://10.0.0.5:4533",
        username = "gavin", password = "secret",
    )

    @Test
    fun `no ceiling means no transcoding parameters`() {
        val url = client.streamUrl(server, "song-1", maxBitrateKbps = 0)
        assertTrue("maxBitRate" !in url)
        assertTrue("format" !in url)
        assertTrue("id=song-1" in url)
    }

    @Test
    fun `a ceiling asks the server to transcode`() {
        val url = client.streamUrl(server, "song-1", maxBitrateKbps = 128)
        assertTrue("maxBitRate=128" in url)
        // Several servers only transcode when told what to transcode to.
        assertTrue("format=mp3" in url)
    }

    @Test
    fun `the password is never in the url`() {
        val url = client.streamUrl(server, "song-1")
        assertTrue("Subsonic sends a salted token, never the password", "secret" !in url)
        assertTrue("t=" in url && "s=" in url)
    }

    @Test
    fun `each call salts differently`() {
        // A fixed salt would make the token a password equivalent that could be
        // replayed forever from a captured URL.
        val first = client.streamUrl(server, "song-1")
        val second = client.streamUrl(server, "song-1")
        assertTrue(first != second)
    }
}
