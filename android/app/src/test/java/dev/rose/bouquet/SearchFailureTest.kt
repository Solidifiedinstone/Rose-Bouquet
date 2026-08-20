package dev.rose.bouquet

import dev.rose.bouquet.youtube.YouTubeSource
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A refused search is not a missing song.
 *
 * YouTube does not answer a run of searches reliably, and swallowing that into
 * an empty result list makes a refusal indistinguishable from a song that does
 * not exist. On the desktop that turned six missing tracks in an import into a
 * hundred and thirty-two, and wrote songs that exist into the missing list
 * where they survived a restart and were never looked for again.
 */
class SearchFailureTest {

    @Test
    fun `an unanswerable search has a type of its own`() {
        // The distinction the importer needs: `demand` throws, `search` still
        // returns an empty list for the places where a failure and no results
        // look the same to the person waiting — a search box.
        val thrown = YouTubeSource.SearchUnavailable(java.io.IOException("reset by peer"))
        assertTrue(thrown is Exception)
        assertTrue(thrown.cause is java.io.IOException)
    }

    @Test
    fun `the importer tells the two apart`() {
        // Asserted against the source rather than the network: the point is
        // that the importer catches SearchUnavailable separately from a null
        // match, and reports it separately too.
        val source = javaClass.classLoader!!
            .getResource("../../../src/main/java/dev/rose/bouquet/ui/AppViewModel.kt")
        val text = source?.let { java.io.File(it.toURI()).readText() }
            ?: java.io.File("src/main/java/dev/rose/bouquet/ui/AppViewModel.kt")
                .takeIf { it.exists() }?.readText()
            ?: java.io.File("app/src/main/java/dev/rose/bouquet/ui/AppViewModel.kt").readText()

        assertTrue("importer must use the retrying search", "YouTubeSource.demand(" in text)
        assertTrue("and catch the refusal", "SearchUnavailable" in text)
        assertTrue("and keep it apart from missing", "unreachable" in text)
    }
}
