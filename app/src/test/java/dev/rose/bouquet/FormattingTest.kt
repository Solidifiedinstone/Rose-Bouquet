package dev.rose.bouquet

import dev.rose.bouquet.data.Server
import dev.rose.bouquet.ui.asClock
import dev.rose.bouquet.ui.asCount
import dev.rose.bouquet.ui.theme.ROSE_STYLES
import dev.rose.bouquet.ui.theme.ROSE_THEMES
import dev.rose.bouquet.ui.theme.themeFor
import dev.rose.bouquet.youtube.videoId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FormattingTest {

    @Test
    fun `durations read as clocks`() {
        assertEquals("0:07", 7.asClock())
        assertEquals("3:04", 184.asClock())
        assertEquals("1:00:00", 3600.asClock())
        assertEquals("2:03:04", 7384.asClock())
    }

    @Test
    fun `an unknown duration is not shown as zero`() {
        // Servers report 0 for a track they have not scanned properly, and
        // "0:00" next to a song that plays for four minutes reads as a bug.
        assertEquals("—", 0.asClock())
        assertEquals("—", (-5).asClock())
    }

    @Test
    fun `view counts are shortened the way a person would say them`() {
        assertEquals("999", 999L.asCount())
        assertEquals("1.5K", 1500L.asCount())
        assertEquals("2.3M", 2_300_000L.asCount())
        assertEquals("1.2B", 1_200_000_000L.asCount())
    }
}

class VideoIdTest {

    @Test
    fun `a watch url yields its id`() {
        assertEquals("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ".videoId())
    }

    @Test
    fun `extra query parameters do not end up in the id`() {
        assertEquals(
            "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=RDxyz".videoId(),
        )
    }

    @Test
    fun `shorts and short links put the id in the path`() {
        assertEquals("abc123XYZ_-", "https://www.youtube.com/shorts/abc123XYZ_-".videoId())
        assertEquals("abc123XYZ_-", "https://youtu.be/abc123XYZ_-?si=tracking".videoId())
    }
}

class ServerTest {

    private fun server(name: String, url: String) =
        Server(id = "x", name = name, url = url, username = "u", password = "p")

    @Test
    fun `a named server shows its name`() {
        assertEquals("Front room", server("Front room", "http://10.0.0.5:4533").displayName)
    }

    @Test
    fun `an unnamed server falls back to its host`() {
        assertEquals("10.0.0.5:4533", server("", "http://10.0.0.5:4533").displayName)
        assertEquals("music.example.com", server("", "https://music.example.com/").displayName)
    }
}

/**
 * The generated palettes. These would catch a bad regeneration — a truncated
 * file, or a theme whose lightness was derived the wrong way round and so
 * renders unreadable rather than merely odd.
 */
class ThemeTest {

    @Test
    fun `every theme and style from the desktop app came across`() {
        assertEquals(25, ROSE_THEMES.size)
        assertEquals(11, ROSE_STYLES.size)
    }

    @Test
    fun `theme keys are unique`() {
        assertEquals(ROSE_THEMES.size, ROSE_THEMES.map { it.key }.distinct().size)
        assertEquals(ROSE_STYLES.size, ROSE_STYLES.map { it.key }.distinct().size)
    }

    @Test
    fun `light themes are recognised as light`() {
        listOf("light", "catppuccin-latte", "rose-pine-dawn", "solarized-light").forEach { key ->
            val theme = ROSE_THEMES.first { it.key == key }
            assertTrue("$key should be a light theme", theme.isLight)
        }
    }

    @Test
    fun `dark themes are recognised as dark`() {
        listOf("rose-dark", "rose-oled", "gruvbox", "dracula", "matrix").forEach { key ->
            val theme = ROSE_THEMES.first { it.key == key }
            assertTrue("$key should be a dark theme", !theme.isLight)
        }
    }

    @Test
    fun `an unknown theme falls back to one of the right lightness`() {
        assertTrue(themeFor("no-such-theme", dark = true).isLight.not())
        assertTrue(themeFor("no-such-theme", dark = false).isLight)
    }

    @Test
    fun `a known theme is returned whatever the system is set to`() {
        assertEquals("gruvbox", themeFor("gruvbox", dark = false).key)
        assertEquals("gruvbox", themeFor("gruvbox", dark = true).key)
    }

    @Test
    fun `rose dark is the first theme, so it is the default`() {
        assertEquals("rose-dark", ROSE_THEMES.first().key)
        assertNotNull(ROSE_STYLES.firstOrNull { it.key == "rounded" })
    }
}
