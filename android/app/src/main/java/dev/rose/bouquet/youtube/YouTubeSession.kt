package dev.rose.bouquet.youtube

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import dev.rose.bouquet.data.settingsDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/**
 * Being signed in to YouTube, on a phone.
 *
 * Google will not authenticate an app. It refuses an embedded browser outright
 * — "this browser or app may not be secure" — and no user agent, address or
 * second window argues with that. The desktop gets round it by reading the
 * session out of a browser on the same machine; a phone cannot, because
 * Android sandboxes every app's data and no permission opens another app's
 * cookie store.
 *
 * So the session arrives one of two ways, and both are things you do on
 * purpose:
 *
 * * **From your own Rose Bouquet server.** The desktop already has a real
 *   session and this phone already pairs with it, so it asks. One tap, and it
 *   works while your desktop is reachable.
 *
 * * **Pasted.** The `Cookie:` header out of a browser's developer tools. Ugly,
 *   and the only thing that works with no server — which is why NouTube, which
 *   ships and can be signed into, offers exactly this and calls it what it is.
 *
 * Stored on the phone and nowhere else, and sent only to YouTube and Google —
 * see `NewPipeDownloader`.
 */
object YouTubeSession {

    private val key = stringPreferencesKey("youtube_session")

    /** The stored session, as a `Cookie:` header. Empty means signed out. */
    fun stored(context: Context): Flow<String> =
        context.settingsDataStore.data.map { it[key].orEmpty() }

    /**
     * Whether this looks like a signed-in session rather than a visited one.
     *
     * The same test the desktop uses: a browser that has been to YouTube has
     * plenty of cookies and none of these. Checking is what turns "it did not
     * work" into "that browser is not signed in", which is a thing somebody
     * can act on.
     */
    fun signedIn(cookie: String): Boolean =
        AUTH_COOKIES.any { name -> Regex("(^|;\\s*)${Regex.escape(name)}=").containsMatchIn(cookie) }

    suspend fun remember(context: Context, cookie: String) {
        context.settingsDataStore.edit { it[key] = cookie.trim() }
        apply(cookie.trim())
    }

    suspend fun forget(context: Context) {
        context.settingsDataStore.edit { it.remove(key) }
        apply("")
    }

    /** Put a session into effect for everything the extractor does next. */
    fun apply(cookie: String) {
        NewPipeDownloader.session = cookie.takeIf { it.isNotBlank() }
    }

    /**
     * The cookies that actually constitute being signed in to Google.
     *
     * The same list as the desktop's, so the two halves agree about what
     * "signed in" means.
     */
    private val AUTH_COOKIES = listOf(
        "SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO",
        "__Secure-1PSID", "__Secure-3PSID",
    )
}
