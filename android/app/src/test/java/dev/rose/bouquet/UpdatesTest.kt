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

    /**
     * A file in the project, found from wherever the tests happen to run.
     *
     * Gradle runs unit tests with the module directory as the working
     * directory, which is not where anybody writing a relative path expects to
     * be. Walking up until the file appears works from the module, the Android
     * project or the repository root alike.
     */
    private fun projectFile(relative: String): java.io.File {
        var here: java.io.File? = java.io.File("").absoluteFile
        while (here != null) {
            val candidate = java.io.File(here, relative)
            if (candidate.exists()) return candidate
            here = here.parentFile
        }
        throw AssertionError("could not find $relative from ${java.io.File("").absolutePath}")
    }

    @Test
    fun `updates come from the one repository both halves are released from`() {
        // The phone client moved into `android/` in the desktop repository and
        // the two go out in the same release. Left pointing at the old
        // Android-only repository, this would watch a stream that has stopped
        // and report "newest version" for ever — the quietest way an updater
        // can be broken.
        val text = projectFile(
            "app/src/main/java/dev/rose/bouquet/data/Updates.kt").readText()

        assertTrue(
            "the updater must read the merged repository's releases",
            "repos/Solidifiedinstone/Rose-Bouquet/releases" in text,
        )
        assertTrue(
            "and not the repository the client used to live in",
            "Rose-Bouquet-Android" !in text,
        )
    }

    @Test
    fun `the asset it looks for is the one the release script publishes`() {
        // A release carries the desktop wheel, an sdist and the APK. The
        // updater picks by extension, so the two only agree by convention —
        // and a convention nothing checks is one that drifts.
        assertTrue(
            "the release script publishes an .apk",
            ".apk" in projectFile("tools/release.sh").readText(),
        )
        assertTrue(
            "and the updater picks an asset by that extension",
            "endsWith(\".apk\")" in projectFile(
                "app/src/main/java/dev/rose/bouquet/data/Updates.kt").readText(),
        )
    }
}
