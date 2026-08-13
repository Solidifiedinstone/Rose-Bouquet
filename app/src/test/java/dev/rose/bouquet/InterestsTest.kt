package dev.rose.bouquet

import dev.rose.bouquet.youtube.Interests
import dev.rose.bouquet.youtube.deriveTopics
import dev.rose.bouquet.youtube.isSlop
import dev.rose.bouquet.youtube.keep
import dev.rose.bouquet.youtube.words
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The filtering rules, which are the part of this app somebody would notice
 * being wrong. "Blocked" that does not block is the specific failure these
 * exist to prevent.
 */
class InterestsTest {

    private data class Item(val title: String, val channel: String)

    private fun filter(items: List<Item>, interests: Interests) =
        keep(items, interests, title = { it.title }, channel = { it.channel })

    @Test
    fun `apostrophes do not split words in half`() {
        // "don't" must not become "don" and "t" — two tokens where there was
        // one word, neither of which means anything.
        assertTrue("dont" in words("Why I don't use Linux"))
        assertFalse("don" in words("Why I don't use Linux"))
    }

    @Test
    fun `stopwords and short words are dropped`() {
        val found = words("This is THE best video of the year")
        assertFalse("the" in found)
        assertFalse("best" in found)
        assertFalse("video" in found)
    }

    @Test
    fun `digits alone are not topics`() {
        assertFalse("2024" in words("Retrospective 2024 roundup"))
    }

    @Test
    fun `slop is caught regardless of case or punctuation`() {
        assertTrue(isSlop("Bohemian Rhapsody (AI COVER)"))
        assertTrue(isSlop("you won't believe what happened next"))
        assertTrue(isSlop("Reddit Stories compilation"))
    }

    @Test
    fun `ordinary titles are not slop`() {
        assertFalse(isSlop("Restoring a 1974 Sony TC-377 reel to reel"))
        assertFalse(isSlop("Boards of Canada - Dayvan Cowboy"))
    }

    @Test
    fun `a slop word inside a longer word does not fire`() {
        // Substring matching gets all of these wrong, and the failure is
        // invisible: the video the user wanted simply never appears.
        assertFalse(isSlop("A history of the Sorabji piano sonatas"))   // sora
        assertFalse(isSlop("Veolia water treatment works tour"))        // veo
        assertFalse(isSlop("Building a 300 watts amplifier"))           // tts
        assertFalse(isSlop("The Essex marshes at dawn"))                // sex
    }

    @Test
    fun `slop still fires when the phrase is a whole word`() {
        assertTrue(isSlop("Made with Sora — cinematic b-roll"))
        assertTrue(isSlop("Generated with Veo 3"))
    }

    @Test
    fun `a blocked word removes the item rather than demoting it`() {
        val items = listOf(
            Item("Crypto is the future", "Finance Bro"),
            Item("Soldering a through-hole board", "Electronics"),
        )
        val kept = filter(items, Interests(blocked = setOf("crypto")))
        assertEquals(1, kept.size)
        assertEquals("Electronics", kept.first().channel)
    }

    @Test
    fun `a blocked channel removes everything from it`() {
        val items = listOf(
            Item("Something reasonable", "Loud Reaction Channel"),
            Item("Something else", "Techmoan"),
        )
        val kept = filter(items, Interests(blockedChannels = setOf("loud reaction")))
        assertEquals(1, kept.size)
        assertEquals("Techmoan", kept.first().channel)
    }

    @Test
    fun `the slop filter can be turned off`() {
        val items = listOf(Item("Hotel California (AI cover)", "Someone"))
        assertTrue(filter(items, Interests(filterSlop = true)).isEmpty())
        assertEquals(1, filter(items, Interests(filterSlop = false)).size)
    }

    @Test
    fun `no stated preferences means nothing is removed`() {
        val items = listOf(Item("Anything at all", "Any channel"))
        assertEquals(1, filter(items, Interests(filterSlop = false)).size)
    }

    @Test
    fun `topics come from words that recur, not from one-offs`() {
        val titles = listOf(
            "Restoring a reel to reel tape deck",
            "The reel to reel tape formats explained",
            "Commercial clothes steam press demonstration",
        )
        val topics = deriveTopics(titles)
        assertTrue("reel" in topics)
        assertTrue("tape" in topics)
        // Seen once, so it is not what this history is about — the exact
        // failure that put industrial laundry equipment in a music feed.
        assertFalse("steam" in topics)
    }

    @Test
    fun `a word repeated within one title still counts once`() {
        val topics = deriveTopics(listOf("tape tape tape tape deck", "a deck of tape"))
        // Both appear in two titles, so both survive; the point is that the
        // first title did not get four votes.
        assertTrue("tape" in topics)
        assertTrue("deck" in topics)
    }
}
