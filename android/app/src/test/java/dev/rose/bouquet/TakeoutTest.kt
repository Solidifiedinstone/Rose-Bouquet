package dev.rose.bouquet

import dev.rose.bouquet.data.Imports
import dev.rose.bouquet.data.db.WatchEntity
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.BufferedReader
import java.io.StringReader

/**
 * The Takeout parsers.
 *
 * These run over a reader rather than a String because the real file is
 * hundreds of megabytes and reading it whole is an out-of-memory kill on a
 * phone — which is what "I couldn't import" turned out to mean. The tests feed
 * them the same way the importer does.
 */
class TakeoutTest {

    private fun readJson(text: String): List<WatchEntity> = runBlocking {
        val found = mutableListOf<WatchEntity>()
        Imports.streamJson(BufferedReader(StringReader(text))) { found += it }
        found
    }

    private fun readHtml(text: String): List<WatchEntity> = runBlocking {
        val found = mutableListOf<WatchEntity>()
        Imports.streamHtml(BufferedReader(StringReader(text))) { found += it }
        found
    }

    // ── JSON ──────────────────────────────────────────────────────

    @Test
    fun `json records come out one at a time`() {
        val records = readJson("""
            [{"header":"YouTube","title":"Watched Dayvan Cowboy",
              "titleUrl":"https://www.youtube.com/watch?v=abc123",
              "subtitles":[{"name":"Boards of Canada",
                            "url":"https://www.youtube.com/channel/UCxyz"}],
              "time":"2026-03-04T18:22:11.123Z"},
             {"header":"YouTube","title":"Watched Roygbiv",
              "titleUrl":"https://www.youtube.com/watch?v=def456",
              "subtitles":[{"name":"Boards of Canada",
                            "url":"https://www.youtube.com/channel/UCxyz"}],
              "time":"2026-03-04T18:30:00.000Z"}]
        """.trimIndent())

        assertEquals(2, records.size)
        assertEquals("abc123", records[0].videoId)
        // "Watched " is Google's prefix, not part of the title — left in, it
        // becomes a topic in its own right.
        assertEquals("Dayvan Cowboy", records[0].title)
        assertEquals("Boards of Canada", records[0].channel)
        assertEquals("UCxyz", records[0].channelId)
    }

    @Test
    fun `a brace inside a title does not split a record`() {
        val records = readJson("""
            [{"title":"Watched what } { even is this",
              "titleUrl":"https://www.youtube.com/watch?v=aaa"}]
        """.trimIndent())
        assertEquals(1, records.size)
        assertEquals("aaa", records[0].videoId)
    }

    @Test
    fun `an escaped quote does not confuse the scanner`() {
        val records = readJson("""
            [{"title":"Watched the \"best\" one",
              "titleUrl":"https://www.youtube.com/watch?v=bbb"}]
        """.trimIndent())
        assertEquals(1, records.size)
        assertEquals("the \"best\" one", records[0].title)
    }

    @Test
    fun `shorts are marked as shorts`() {
        val records = readJson("""
            [{"title":"Watched a short",
              "titleUrl":"https://www.youtube.com/shorts/shortid1"}]
        """.trimIndent())
        assertEquals(1, records.size)
        assertEquals("shortid1", records[0].videoId)
        assertTrue(records[0].isShort)
    }

    @Test
    fun `entries with no video url are skipped rather than throwing`() {
        val records = readJson("""
            [{"title":"Visited YouTube Music"},
             {"title":"Watched something",
              "titleUrl":"https://www.youtube.com/watch?v=ccc"}]
        """.trimIndent())
        assertEquals(1, records.size)
    }

    // ── HTML ──────────────────────────────────────────────────────

    private fun htmlCell(id: String, title: String, channel: String) = """
        <div class="content-cell mdl-cell mdl-cell--6-col">
          <a href="https://www.youtube.com/watch?v=$id">$title</a><br>
          <a href="https://www.youtube.com/channel/UC$channel">$channel</a><br>
          Mar 4, 2026, 6:22:11 PM GMT
        </div>
    """.trimIndent()

    @Test
    fun `html cells are read one at a time`() {
        val page = "<html><body>" +
            htmlCell("abc123", "Dayvan Cowboy", "Boards") +
            htmlCell("def456", "Roygbiv", "Boards") +
            "</body></html>"

        val records = readHtml(page)
        assertEquals(2, records.size)
        assertEquals("abc123", records[0].videoId)
        assertEquals("Dayvan Cowboy", records[0].title)
        assertEquals("Boards", records[0].channel)
    }

    @Test
    fun `the final cell is not lost`() {
        // The scanner cuts on the start of the *next* cell, so the last one has
        // no successor to cut against and is easy to drop.
        val records = readHtml("<html><body>" + htmlCell("only1", "Alone", "Ch") + "</body></html>")
        assertEquals(1, records.size)
        assertEquals("only1", records[0].videoId)
    }

    @Test
    fun `entities in titles are unescaped`() {
        val records = readHtml("<html><body>" +
            htmlCell("ent1", "Rock &amp; Roll", "Ch") + "</body></html>")
        assertEquals("Rock & Roll", records[0].title)
    }

    @Test
    fun `a cell spanning a read boundary is still whole`() {
        // The reader fills in 64 KB chunks, so a record straddling a chunk
        // boundary is the normal case in a real file, not an edge case.
        val filler = "x".repeat(70_000)
        val page = "<html><body><!--$filler-->" +
            htmlCell("split1", "Across The Boundary", "Ch") +
            htmlCell("split2", "Second", "Ch") + "</body></html>"

        val records = readHtml(page)
        assertEquals(2, records.size)
        assertEquals("Across The Boundary", records[0].title)
    }

    @Test
    fun `a page with no history cells yields nothing and does not hang`() {
        assertTrue(readHtml("<html><body><p>nothing here</p></body></html>").isEmpty())
    }

    // ── When it was watched ───────────────────────────────────────

    /**
     * The date is inside the cell, and separated from AM/PM by U+202F.
     *
     * Both halves of this were real: reading only the body's own text found no
     * date at all, and Google's narrow no-break space defeats the ordinary
     * "h:mm:ss a" pattern. Either one alone stamps the whole imported history
     * with the moment of the import, which leaves the recommender with three
     * hundred videos all watched at the same second and no idea what is recent.
     */
    @Test
    fun `an html cell carries the time it was watched`() {
        val record = readHtml(cell("Mar 4, 2026, 12:34:56\u202fPM PST")).single()
        assertTrue(
            "the timestamp was not read; it fell back to the import time",
            record.watchedAt < System.currentTimeMillis() - 60_000,
        )
        assertEquals(2026, yearOf(record.watchedAt))
    }

    @Test
    fun `an ordinary space before the marker parses too`() {
        val record = readHtml(cell("Mar 4, 2026, 12:34:56 PM PST")).single()
        assertEquals(2026, yearOf(record.watchedAt))
    }

    @Test
    fun `a cell with no date falls back to now rather than failing`() {
        val record = readHtml(cell("")).single()
        assertTrue(record.watchedAt > System.currentTimeMillis() - 60_000)
    }

    @Test
    fun `json timestamps are read`() {
        val record = readJson("""
            [{"title":"Watched Something","titleUrl":"https://www.youtube.com/watch?v=abc123",
              "time":"2021-06-01T09:00:00.000Z"}]
        """.trimIndent()).single()
        assertEquals(2021, yearOf(record.watchedAt))
    }

    private fun cell(date: String) =
        "<div class=\"content-cell mdl-cell--6-col\">Watched&nbsp;" +
            "<a href=\"https://www.youtube.com/watch?v=abc123\">Dayvan Cowboy</a><br>" +
            "<a href=\"https://www.youtube.com/channel/UCxyz\">Boards of Canada</a><br>" +
            date + "</div>"

    private fun yearOf(millis: Long) = java.util.Calendar.getInstance()
        .apply { timeInMillis = millis }
        .get(java.util.Calendar.YEAR)
}
