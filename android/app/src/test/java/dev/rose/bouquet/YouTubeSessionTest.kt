package dev.rose.bouquet

import dev.rose.bouquet.youtube.NewPipeDownloader
import dev.rose.bouquet.youtube.YouTubeSession
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Being signed in to YouTube on a phone.
 *
 * Google will not authenticate an app, and Android will not let one read
 * another app's cookie jar, so a session has to arrive from somewhere — the
 * desktop over the network, or pasted. What matters here is telling a real
 * session from a browser that has merely visited YouTube, and never attaching
 * one to a request that is not YouTube's.
 */
class YouTubeSessionTest {

    @After
    fun clear() {
        NewPipeDownloader.session = null
    }

    @Test
    fun `a session is told from a browser that merely visited`() {
        // What a signed-out browser accumulates: plenty of cookies, none of
        // the ones a Google sign-in is made of.
        assertFalse(YouTubeSession.signedIn("PREF=f1=50000000; VISITOR_INFO1_LIVE=abc; YSC=xyz"))
        assertFalse(YouTubeSession.signedIn(""))
        assertFalse(YouTubeSession.signedIn("SIDEBAR=1; NOTSID=2"))

        assertTrue(YouTubeSession.signedIn("SID=abc; HSID=def"))
        assertTrue(YouTubeSession.signedIn("PREF=x; SAPISID=abc"))
        assertTrue(YouTubeSession.signedIn("__Secure-1PSID=abc"))
        assertTrue(YouTubeSession.signedIn("LOGIN_INFO=abc"))
    }

    @Test
    fun `a name that merely contains an auth cookie is not one`() {
        // `NOTSID=` and `MYSAPISID=` must not read as a sign-in — the check is
        // anchored to the start of a cookie, not a substring search.
        assertFalse(YouTubeSession.signedIn("MYSAPISID=abc; XSID=def"))
        assertTrue(YouTubeSession.signedIn("first=1; SID=abc"))
    }

    @Test
    fun `applying a session puts it where the extractor reads it`() {
        YouTubeSession.apply("SID=abc")
        assertEquals("SID=abc", NewPipeDownloader.session)

        // Signing out clears it rather than leaving an empty header on every
        // request.
        YouTubeSession.apply("")
        assertNull(NewPipeDownloader.session)
        YouTubeSession.apply("   ")
        assertNull(NewPipeDownloader.session)
    }
}
