package dev.rose.bouquet.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/**
 * What the app remembers: which server, and how it should look.
 *
 * The server password is stored in the same place as everything else, which is
 * a deliberate limit rather than an oversight: this is a credential for a music
 * server on your own network, and putting it behind the keystore would add a
 * biometric prompt to opening a music app. If that trade is wrong for you, the
 * desktop server can be run without a password at all on a network you trust.
 */
private val Context.store by preferencesDataStore(name = "rose-bouquet")

class Settings(private val context: Context) {

    data class State(
        val serverUrl: String = "",
        val username: String = "rose",
        val password: String = "",
        val themeKey: String = "rose-dark",
        val styleKey: String = "rounded",
    ) {
        val configured: Boolean get() = serverUrl.isNotBlank()
    }

    val state: Flow<State> = context.store.data.map { prefs ->
        State(
            serverUrl = prefs[SERVER_URL].orEmpty(),
            username = prefs[USERNAME] ?: "rose",
            password = prefs[PASSWORD].orEmpty(),
            themeKey = prefs[THEME] ?: "rose-dark",
            styleKey = prefs[STYLE] ?: "rounded",
        )
    }

    suspend fun setServer(url: String, username: String, password: String) {
        context.store.edit { prefs ->
            // Trailing slashes are the most common paste error and cost nothing
            // to forgive.
            prefs[SERVER_URL] = url.trim().trimEnd('/')
            prefs[USERNAME] = username.trim()
            prefs[PASSWORD] = password
        }
    }

    suspend fun setAppearance(themeKey: String, styleKey: String) {
        context.store.edit { prefs ->
            prefs[THEME] = themeKey
            prefs[STYLE] = styleKey
        }
    }

    suspend fun forgetServer() {
        context.store.edit { prefs ->
            prefs.remove(SERVER_URL)
            prefs.remove(PASSWORD)
        }
    }

    private companion object {
        val SERVER_URL = stringPreferencesKey("server_url")
        val USERNAME = stringPreferencesKey("username")
        val PASSWORD = stringPreferencesKey("password")
        val THEME = stringPreferencesKey("theme")
        val STYLE = stringPreferencesKey("style")
    }
}
