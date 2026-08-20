package dev.rose.bouquet

import dev.rose.bouquet.data.Updates
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class UpdatesTest {

    @Test
    fun `versions compare as numbers, not as strings`() {
        // The one that matters: as strings, "0.1.10" sorts before "0.1.9", so
        // the update stops being offered exactly when releases start piling up.
        assertTrue(Updates.isNewer("0.1.10", "0.1.9"))
        assertTrue(Updates.isNewer("0.2.0", "0.1.99"))
        assertTrue(Updates.isNewer("1.0.0", "0.9.9"))
    }

    @Test
    fun `the same or older is not an update`() {
        assertFalse(Updates.isNewer("0.1.3", "0.1.3"))
        assertFalse(Updates.isNewer("0.1.2", "0.1.3"))
        assertFalse(Updates.isNewer("0.9.9", "1.0.0"))
    }

    @Test
    fun `suffixes and short versions do not confuse it`() {
        assertFalse(Updates.isNewer("0.1.3-debug", "0.1.3"))
        assertTrue(Updates.isNewer("0.2", "0.1.9"))
        assertFalse(Updates.isNewer("0.1", "0.1.0"))
    }
}
