package dev.rose.bouquet

import dev.rose.bouquet.data.LibraryOrder
import dev.rose.bouquet.data.db.SongEntity
import dev.rose.bouquet.data.inOrder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The library orders, which have to agree with the desktop's.
 *
 * Both halves of Rose Bouquet sort a library the same way, so the same list on
 * a phone and on a desktop is in the same order. The one deliberate difference
 * is that this has no "Most played": a Subsonic library carries no play count,
 * and deriving one from what happens to be downloaded would order the library
 * by storage rather than by listening.
 */
class LibraryOrderTest {

    private fun song(
        title: String,
        artist: String,
        album: String = "An album",
        seconds: Int = 200,
        track: Int? = 1,
        seenAt: Long = 0,
        starred: Boolean = false,
    ) = SongEntity(
        serverId = "s", id = title, title = title, artist = artist, album = album,
        albumId = null, durationSeconds = seconds, track = track, year = null,
        coverArt = null, suffix = null, sizeBytes = null, starred = starred,
        seenAt = seenAt,
    )

    private val library = listOf(
        song("Bravo", "Zeta", album = "Later", seconds = 200, track = 2, seenAt = 2),
        song("Alpha", "Alpha", album = "Early", seconds = 90, seenAt = 30, starred = true),
        song("Charlie", "Mid", album = "Middle", seconds = 400, seenAt = 20),
    )

    private fun titles(order: LibraryOrder) = library.inOrder(order).map { it.title }

    @Test
    fun `every order does what it says`() {
        assertEquals(listOf("Alpha", "Charlie", "Bravo"), titles(LibraryOrder.ArtistAsc))
        assertEquals(listOf("Bravo", "Charlie", "Alpha"), titles(LibraryOrder.ArtistDesc))
        assertEquals(listOf("Alpha", "Bravo", "Charlie"), titles(LibraryOrder.TitleAsc))
        assertEquals(listOf("Charlie", "Bravo", "Alpha"), titles(LibraryOrder.TitleDesc))
        assertEquals(listOf("Alpha", "Bravo", "Charlie"), titles(LibraryOrder.Album))
        assertEquals(listOf("Charlie", "Bravo", "Alpha"), titles(LibraryOrder.Longest))
        assertEquals(listOf("Alpha", "Bravo", "Charlie"), titles(LibraryOrder.Shortest))
        assertEquals(listOf("Alpha", "Charlie", "Bravo"), titles(LibraryOrder.RecentlyAdded))
        assertEquals("Alpha", titles(LibraryOrder.Starred).first())
    }

    @Test
    fun `no order loses or invents a track`() {
        LibraryOrder.entries.forEach { order ->
            assertEquals(
                order.name,
                library.map { it.title }.sorted(),
                library.inOrder(order).map { it.title }.sorted(),
            )
        }
    }

    @Test
    fun `a track with no duration is unknown rather than nothing`() {
        // Shortest-first must not be a list of everything unread, then the music.
        val songs = listOf(song("Known", "A", seconds = 120), song("Unread", "A", seconds = 0))
        assertEquals(
            listOf("Known", "Unread"),
            songs.inOrder(LibraryOrder.Shortest).map { it.title },
        )
    }

    @Test
    fun `an order nobody recognises is the default, not a crash`() {
        // The order is a saved preference, and one written by a newer version
        // must not stop an older one from showing you your music.
        assertEquals(LibraryOrder.Default, LibraryOrder.of("by-vibes"))
        assertEquals(LibraryOrder.Default, LibraryOrder.of(null))
        assertEquals(LibraryOrder.ArtistAsc, LibraryOrder.of("ArtistAsc"))
    }

    @Test
    fun `ties fall back to the artist ordering`() {
        // Two songs of the same length still come out grouped by artist and
        // album rather than in whatever order the database handed them over.
        val songs = listOf(
            song("Second", "Beta", album = "B", seconds = 100),
            song("First", "Alpha", album = "A", seconds = 100),
        )
        assertEquals(listOf("First", "Second"), songs.inOrder(LibraryOrder.Longest).map { it.title })
        assertTrue(songs.inOrder(LibraryOrder.Shortest).first().artist == "Alpha")
    }
}
