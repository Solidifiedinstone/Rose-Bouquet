package dev.rose.bouquet.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dev.rose.bouquet.ui.theme.SYSTEM_THEME
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.settingsDataStore: DataStore<Preferences> by preferencesDataStore("settings")

/**
 * Everything the user has chosen, in one place.
 *
 * Small values only. Anything that grows — history, the feed, the library
 * cache — lives in Room, because DataStore rewrites the whole file on every
 * write and a 400 KB rewrite per scroll is a real bug the desktop app shipped
 * once already.
 */
data class Settings(
    val theme: String = SYSTEM_THEME,
    val style: String = "rounded",
    /** Stream quality ceiling in kbps; 0 means whatever the server sends. */
    val maxBitrate: Int = 0,
    /** Only stream when on wifi, so mobile data is downloads-only. */
    val wifiOnlyStreaming: Boolean = false,
    /** Download over mobile data as well as wifi. */
    val downloadOnMobile: Boolean = false,
    /** Tell the server what was played. */
    val scrobble: Boolean = true,
    /** Filter engagement-bait and generated slop out of recommendations. */
    val filterSlop: Boolean = true,
    /** Topics to see more of. */
    val interests: Set<String> = emptySet(),
    /** Topics to never see. */
    val blocked: Set<String> = emptySet(),
    /** Channels to never see, by name. */
    val blockedChannels: Set<String> = emptySet(),
    /** Keep the video half of the app out of the way entirely. */
    val musicOnly: Boolean = false,
)

class SettingsStore(private val context: Context) {

    private object Keys {
        val theme = stringPreferencesKey("theme")
        val style = stringPreferencesKey("style")
        val maxBitrate = intPreferencesKey("max_bitrate")
        val wifiOnlyStreaming = booleanPreferencesKey("wifi_only_streaming")
        val downloadOnMobile = booleanPreferencesKey("download_on_mobile")
        val scrobble = booleanPreferencesKey("scrobble")
        val filterSlop = booleanPreferencesKey("filter_slop")
        val interests = stringSetPreferencesKey("interests")
        val blocked = stringSetPreferencesKey("blocked")
        val blockedChannels = stringSetPreferencesKey("blocked_channels")
        val musicOnly = booleanPreferencesKey("music_only")
    }

    val settings: Flow<Settings> = context.settingsDataStore.data.map { p ->
        val defaults = Settings()
        Settings(
            theme = p[Keys.theme] ?: defaults.theme,
            style = p[Keys.style] ?: defaults.style,
            maxBitrate = p[Keys.maxBitrate] ?: defaults.maxBitrate,
            wifiOnlyStreaming = p[Keys.wifiOnlyStreaming] ?: defaults.wifiOnlyStreaming,
            downloadOnMobile = p[Keys.downloadOnMobile] ?: defaults.downloadOnMobile,
            scrobble = p[Keys.scrobble] ?: defaults.scrobble,
            filterSlop = p[Keys.filterSlop] ?: defaults.filterSlop,
            interests = p[Keys.interests] ?: defaults.interests,
            blocked = p[Keys.blocked] ?: defaults.blocked,
            blockedChannels = p[Keys.blockedChannels] ?: defaults.blockedChannels,
            musicOnly = p[Keys.musicOnly] ?: defaults.musicOnly,
        )
    }

    suspend fun setTheme(key: String) = put(Keys.theme, key)
    suspend fun setStyle(key: String) = put(Keys.style, key)
    suspend fun setMaxBitrate(kbps: Int) = put(Keys.maxBitrate, kbps)
    suspend fun setWifiOnlyStreaming(on: Boolean) = put(Keys.wifiOnlyStreaming, on)
    suspend fun setDownloadOnMobile(on: Boolean) = put(Keys.downloadOnMobile, on)
    suspend fun setScrobble(on: Boolean) = put(Keys.scrobble, on)
    suspend fun setFilterSlop(on: Boolean) = put(Keys.filterSlop, on)
    suspend fun setInterests(values: Set<String>) = put(Keys.interests, values)
    suspend fun setBlocked(values: Set<String>) = put(Keys.blocked, values)
    suspend fun setBlockedChannels(values: Set<String>) = put(Keys.blockedChannels, values)
    suspend fun setMusicOnly(on: Boolean) = put(Keys.musicOnly, on)

    private suspend fun <T> put(key: Preferences.Key<T>, value: T) {
        context.settingsDataStore.edit { it[key] = value }
    }
}
