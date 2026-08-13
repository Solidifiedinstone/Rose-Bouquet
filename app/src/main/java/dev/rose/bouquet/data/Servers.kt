package dev.rose.bouquet.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * One server you can play from.
 *
 * [id] is generated once and never derived from the address, so renaming a
 * server or moving it to a new hostname keeps whatever is downloaded from it
 * attached to it rather than orphaning the lot.
 */
@Serializable
data class Server(
    val id: String,
    val name: String,
    val url: String,
    val username: String,
    val password: String,
    /** Send the password in the clear instead of Subsonic's salted token. */
    val plaintextPassword: Boolean = false,
) {
    /** What to show when the server has no name of its own. */
    val displayName: String
        get() = name.ifBlank { url.substringAfter("://").substringBefore('/').ifBlank { "Server" } }
}

@Serializable
private data class ServerFile(
    val servers: List<Server> = emptyList(),
    val activeId: String? = null,
)

private val Context.serverDataStore: DataStore<Preferences> by preferencesDataStore("servers")

/**
 * Every server you have added, and which one is currently in use.
 *
 * Several servers at once rather than one, because the phone is the thing that
 * moves: a home library, a friend's, and a VPS are all normal to have, and
 * re-typing an address and password to switch between them is the kind of
 * friction that means you stop bothering. Switching is one tap, and what is
 * downloaded stays attached to the server it came from.
 *
 * **Credentials.** Subsonic's own scheme requires the client to hold the real
 * password — the salted token is computed per request from it, so there is
 * nothing weaker to store instead. It lives in this app's private storage,
 * which is as far as the protocol allows; a token-based server would let us do
 * better and Subsonic is not one.
 */
class ServerStore(private val context: Context) {

    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val key = stringPreferencesKey("servers")

    private fun read(preferences: Preferences): ServerFile =
        preferences[key]?.let { runCatching { json.decodeFromString<ServerFile>(it) }.getOrNull() }
            ?: ServerFile()

    val servers: Flow<List<Server>> =
        context.serverDataStore.data.map { read(it).servers }

    /**
     * The server in use, or null when none is set up yet.
     *
     * Falls back to the first server rather than to null when the saved active
     * id names something that has been deleted — an app with a library and no
     * selected server should show the library, not an empty setup screen.
     */
    val active: Flow<Server?> = context.serverDataStore.data.map { preferences ->
        val file = read(preferences)
        file.servers.firstOrNull { it.id == file.activeId } ?: file.servers.firstOrNull()
    }

    suspend fun add(server: Server) = context.serverDataStore.edit { preferences ->
        val file = read(preferences)
        val servers = file.servers.filterNot { it.id == server.id } + server
        // First server added becomes the active one; nobody wants to add a
        // server and then be asked to also choose it.
        preferences[key] = json.encodeToString(
            file.copy(servers = servers, activeId = file.activeId ?: server.id))
    }

    suspend fun remove(id: String) = context.serverDataStore.edit { preferences ->
        val file = read(preferences)
        val servers = file.servers.filterNot { it.id == id }
        preferences[key] = json.encodeToString(ServerFile(
            servers = servers,
            activeId = file.activeId?.takeIf { it != id } ?: servers.firstOrNull()?.id,
        ))
    }

    suspend fun setActive(id: String) = context.serverDataStore.edit { preferences ->
        val file = read(preferences)
        if (file.servers.any { it.id == id }) {
            preferences[key] = json.encodeToString(file.copy(activeId = id))
        }
    }
}
