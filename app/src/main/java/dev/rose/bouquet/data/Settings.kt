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
import dev.rose.bouquet.ui.screens.ColourMode
import dev.rose.bouquet.ui.screens.ColourMotion
import dev.rose.bouquet.ui.screens.Layer
import dev.rose.bouquet.ui.screens.Shape
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
    /** Visualiser shapes, drawn back to front. Empty means nothing is drawn. */
    val visualiserLayers: List<Layer> = listOf(Layer(Shape.Bars)),
    /** How hard the visualiser reacts. 1 is unity. */
    val visualiserIntensity: Float = 1f,
    /**
     * Colours for the Solid and Multi modes, as ARGB.
     *
     * Empty means "use the theme", which is what Theme mode always does —
     * so a palette chosen here survives switching to Theme and back.
     */
    val visualiserColours: List<Int> = emptyList(),
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
        val visualiserLayers = stringPreferencesKey("visualiser_layers")
        val visualiserIntensity = androidx.datastore.preferences.core.floatPreferencesKey(
            "visualiser_intensity")
        val visualiserColours = stringPreferencesKey("visualiser_colours")
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
            visualiserLayers = p[Keys.visualiserLayers]?.let(::decodeLayers)
                ?: defaults.visualiserLayers,
            visualiserIntensity = p[Keys.visualiserIntensity] ?: defaults.visualiserIntensity,
            visualiserColours = p[Keys.visualiserColours]
                ?.split(',')?.mapNotNull { it.trim().toIntOrNull() }
                ?: defaults.visualiserColours,
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
    suspend fun setVisualiserIntensity(value: Float) = put(Keys.visualiserIntensity, value)
    suspend fun setVisualiserLayers(layers: List<Layer>) =
        put(Keys.visualiserLayers, encodeLayers(layers))
    suspend fun setVisualiserColours(colours: List<Int>) =
        put(Keys.visualiserColours, colours.joinToString(","))

    private suspend fun <T> put(key: Preferences.Key<T>, value: T) {
        context.settingsDataStore.edit { it[key] = value }
    }
}

/**
 * Layers as one string, `shape:scale:mode:motion` per layer.
 *
 * A tiny format rather than JSON because DataStore stores strings and the
 * alternative is a serialiser for four enums. Anything unparseable is dropped
 * rather than throwing: a settings file from a newer version naming a shape
 * this one has never heard of should cost that layer, not the whole app.
 */
private fun encodeLayers(layers: List<Layer>): String = layers.joinToString(";") {
    "${it.shape.key}:${it.scale}:${it.mode.key}:${it.motion.key}"
}

private fun decodeLayers(raw: String): List<Layer> = raw.split(";").mapNotNull { part ->
    val fields = part.split(":")
    val shape = Shape.entries.firstOrNull { it.key == fields.getOrNull(0) } ?: return@mapNotNull null
    Layer(
        shape = shape,
        scale = fields.getOrNull(1)?.toFloatOrNull() ?: 1f,
        mode = ColourMode.entries.firstOrNull { it.key == fields.getOrNull(2) } ?: ColourMode.Theme,
        motion = ColourMotion.entries.firstOrNull { it.key == fields.getOrNull(3) }
            ?: ColourMotion.Static,
    )
}
