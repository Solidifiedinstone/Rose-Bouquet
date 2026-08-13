package dev.rose.bouquet.data

import android.content.Context
import android.net.Uri
import dev.rose.bouquet.data.db.RoseDatabase
import dev.rose.bouquet.data.db.WatchEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
import java.io.BufferedInputStream
import java.io.BufferedReader
import java.io.InputStreamReader
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.zip.ZipInputStream

/**
 * Bringing history and playlists in from elsewhere.
 *
 * The point of both importers is the same: a recommender built on your watch
 * history is useless on day one and good on day three hundred, and an import
 * is how you skip to day three hundred. The desktop app learned that the hard
 * way — its feed looked broken until a Takeout archive gave it something to
 * work with.
 */
object Imports {

    data class Result(val added: Int, val skipped: Int, val message: String)

    // ── Google Takeout ────────────────────────────────────────────

    /**
     * Read a Google Takeout archive and import the YouTube watch history.
     *
     * Takes the `.zip` straight from Google rather than asking somebody to
     * unpack it first — on a phone there is often nothing to unpack it with.
     * Both formats are handled: the JSON one, and the HTML one Google still
     * hands out by default, which is a 200 MB single page.
     *
     * Videos and shorts are told apart by URL, and stored separately, so an
     * imported history feeds the two tabs the same way a native one does.
     */
    suspend fun takeout(context: Context, uri: Uri): Result = withContext(Dispatchers.IO) {
        val database = RoseDatabase.get(context)
        val youtube = database.youtube()
        val known = youtube.watchedIds().toMutableSet()

        var added = 0
        var skipped = 0
        var sawHistory = false

        val stream = context.contentResolver.openInputStream(uri)
            ?: return@withContext Result(0, 0, "Could not open that file")

        stream.use { input ->
            ZipInputStream(BufferedInputStream(input)).use { zip ->
                while (true) {
                    val entry = zip.nextEntry ?: break
                    val name = entry.name.lowercase()
                    if (entry.isDirectory || !name.contains("watch-history")) continue
                    sawHistory = true

                    // Streamed rather than read whole. A heavy user's
                    // watch-history.html is several hundred megabytes, and
                    // reading it into a String and then into a DOM is an
                    // out-of-memory kill on a phone long before it is an
                    // import. Records come out one at a time and go straight
                    // into the database in batches.
                    val batch = mutableListOf<WatchEntity>()

                    suspend fun flush() {
                        if (batch.isEmpty()) return
                        youtube.watchedAll(batch.toList())
                        batch.clear()
                    }

                    val consume: suspend (WatchEntity) -> Unit = { record ->
                        if (record.videoId in known) {
                            skipped++
                        } else {
                            known += record.videoId
                            batch += record
                            added++
                            if (batch.size >= BATCH) flush()
                        }
                    }

                    // The reader must not close the zip stream between entries.
                    val reader = BufferedReader(
                        InputStreamReader(NonClosing(zip), Charsets.UTF_8), READ_BUFFER)

                    if (name.endsWith(".json")) streamJson(reader, consume)
                    else streamHtml(reader, consume)

                    flush()
                }
            }
        }

        Result(
            added, skipped,
            when {
                !sawHistory ->
                    "No watch-history file in that archive. Ask Takeout for YouTube history " +
                        "specifically — a Takeout of everything else has none."
                added == 0 && skipped == 0 ->
                    "Found the history file but read nothing from it. If this was the HTML " +
                        "export, the JSON one is more reliable."
                added == 0 -> "Already imported — all $skipped were known."
                else -> "Imported $added, skipped $skipped already known."
            },
        )
    }

    /**
     * Emit top-level objects from a JSON array without holding the array.
     *
     * Tracks brace depth and string state so a `{` inside a title cannot be
     * mistaken for the start of a record. Each object is small; the array is
     * not.
     */
    internal suspend fun streamJson(
        reader: BufferedReader,
        emit: suspend (WatchEntity) -> Unit,
    ) {
        val json = Json { ignoreUnknownKeys = true }
        val current = StringBuilder()
        var depth = 0
        var inString = false
        var escaped = false

        while (true) {
            val next = reader.read()
            if (next < 0) break
            val character = next.toChar()

            if (depth > 0) current.append(character)

            when {
                escaped -> escaped = false
                character == '\\' && inString -> escaped = true
                character == '"' -> inString = !inString
                inString -> Unit
                character == '{' -> {
                    if (depth == 0) current.append(character)
                    depth++
                }
                character == '}' -> {
                    depth--
                    if (depth == 0) {
                        val row = runCatching {
                            json.parseToJsonElement(current.toString()).jsonObject
                        }.getOrNull()
                        if (row != null) recordFromJson(row)?.let { emit(it) }
                        current.setLength(0)
                    }
                }
            }
        }
    }

    /**
     * Emit one record per history entry, a cell at a time.
     *
     * Google's HTML is one enormous page of repeated `content-cell` divs. The
     * reader accumulates until a cell is complete, parses that fragment alone,
     * and discards it — so peak memory is one cell rather than one page.
     */
    internal suspend fun streamHtml(
        reader: BufferedReader,
        emit: suspend (WatchEntity) -> Unit,
    ) {
        val buffer = StringBuilder()
        val chunk = CharArray(READ_BUFFER)

        while (true) {
            val read = reader.read(chunk)
            if (read < 0) break
            buffer.append(chunk, 0, read)

            // Cut on the *start* of the next cell, so what is parsed is one
            // whole cell and the partial one stays buffered.
            while (true) {
                val first = buffer.indexOf(CELL)
                if (first < 0) break
                val second = buffer.indexOf(CELL, first + CELL.length)
                if (second < 0) break

                val fragment = buffer.substring(first, second)
                recordFromHtml(fragment)?.let { emit(it) }
                buffer.delete(0, second)
            }

            // A pathological file with no cell markers must not grow forever.
            if (buffer.length > MAX_BUFFER) buffer.delete(0, buffer.length - CELL.length)
        }

        val last = buffer.indexOf(CELL)
        if (last >= 0) recordFromHtml(buffer.substring(last))?.let { emit(it) }
    }

    /** Lets a reader be closed without closing the zip it is reading from. */
    private class NonClosing(private val wrapped: java.io.InputStream) : java.io.InputStream() {
        override fun read() = wrapped.read()
        override fun read(b: ByteArray, off: Int, len: Int) = wrapped.read(b, off, len)
        override fun available() = wrapped.available()
        override fun close() = Unit
    }

    private const val BATCH = 400
    private const val READ_BUFFER = 64 * 1024
    private const val MAX_BUFFER = 4 * 1024 * 1024
    private const val CELL = "<div class=\"content-cell"

    /** One history record from one Takeout JSON object. */
    private fun recordFromJson(row: JsonObject): WatchEntity? {
        val url = row["titleUrl"]?.jsonPrimitive?.contentOrNull ?: return null
        val videoId = url.substringAfter("v=", "").substringBefore('&')
            .ifBlank { url.substringAfterLast('/').substringBefore('?') }
        if (videoId.isBlank()) return null

        val uploader = (row["subtitles"] as? JsonArray)?.firstOrNull() as? JsonObject

        return WatchEntity(
            videoId = videoId,
            // Google prefixes every entry with "Watched ", which is not part
            // of the title and would otherwise become a topic in its own right.
            title = row["title"]?.jsonPrimitive?.contentOrNull.orEmpty()
                .removePrefix("Watched "),
            channel = uploader?.get("name")?.jsonPrimitive?.contentOrNull.orEmpty(),
            channelId = uploader?.get("url")?.jsonPrimitive?.contentOrNull
                ?.substringAfterLast('/'),
            isShort = "/shorts/" in url,
            watchedAt = parseTime(row["time"]?.jsonPrimitive?.contentOrNull),
        )
    }

    /**
     * One history record from one HTML cell.
     *
     * Jsoup on a fragment rather than the page: parsing is the same, and the
     * memory is one entry instead of a few hundred megabytes of DOM.
     */
    private fun recordFromHtml(fragment: String): WatchEntity? {
        val cell = runCatching { Jsoup.parseBodyFragment(fragment) }.getOrNull() ?: return null
        val links = cell.select("a")

        val videoLink = links.firstOrNull {
            "watch?v=" in it.attr("href") || "/shorts/" in it.attr("href")
        } ?: return null

        val href = videoLink.attr("href")
        val videoId = href.substringAfter("v=", "").substringBefore('&')
            .ifBlank { href.substringAfterLast('/').substringBefore('?') }
        if (videoId.isBlank()) return null

        val channelLink = links.firstOrNull { "/channel/" in it.attr("href") }

        return WatchEntity(
            videoId = videoId,
            // Jsoup unescapes entities, which a hand-rolled parser forgets and
            // ends up with "Rock &amp; Roll" in the history.
            title = videoLink.text(),
            channel = channelLink?.text().orEmpty(),
            channelId = channelLink?.attr("href")?.substringAfterLast('/'),
            isShort = "/shorts/" in href,
            watchedAt = parseTime(cell.body().ownText()),
        )
    }

    /**
     * Takeout's timestamps.
     *
     * The HTML export separates the time from the AM/PM marker with a **narrow
     * no-break space** (U+202F), not an ordinary one. Every date silently
     * failed to parse in the desktop app until that was found, which meant an
     * entire imported history carried the import date instead of the real one
     * and looked, to the recommender, like everything was watched at once.
     */
    private fun parseTime(raw: String?): Long {
        val now = System.currentTimeMillis()
        if (raw.isNullOrBlank()) return now

        val cleaned = raw.replace(' ', ' ').replace(' ', ' ').trim()

        FORMATS.forEach { pattern ->
            runCatching {
                return SimpleDateFormat(pattern, Locale.US).parse(cleaned)?.time ?: now
            }
        }
        return now
    }

    private val FORMATS = listOf(
        "yyyy-MM-dd'T'HH:mm:ss.SSSX",
        "yyyy-MM-dd'T'HH:mm:ssX",
        "MMM d, yyyy, h:mm:ss a z",
        "MMM d, yyyy, h:mm:ss a",
        "d MMM yyyy, HH:mm:ss z",
    )

    // ── Spotify ───────────────────────────────────────────────────

    data class Track(val title: String, val artist: String)

    /**
     * Read a public Spotify playlist.
     *
     * Unauthenticated, which caps it at the first 100 tracks — Spotify's public
     * token endpoints are signed now and refuse anonymous requests past that.
     * A capped import says so rather than pretending the playlist was short,
     * and an Exportify CSV is the way around it.
     */
    suspend fun spotifyPlaylist(url: String): List<Track> = withContext(Dispatchers.IO) {
        val id = url.substringAfter("/playlist/", "").substringBefore('?')
        if (id.isBlank()) return@withContext emptyList()

        val request = Request.Builder()
            .url("https://open.spotify.com/playlist/$id")
            .header("User-Agent", "Mozilla/5.0")
            .build()

        val body = runCatching {
            OkHttpClient().newCall(request).execute().use { it.body?.string() }
        }.getOrNull() ?: return@withContext emptyList()

        parsePlaylistPage(body)
    }

    /**
     * Pull titles and artists out of a playlist page.
     *
     * Spotify embeds the playlist in a `<script id="__NEXT_DATA__">` blob, and
     * also emits `music:song` meta tags. The blob carries titles and artists;
     * the meta tags carry only ids, which are useless for finding the same song
     * elsewhere. So the blob is tried first and the meta tags are the fallback
     * that at least reports how long the playlist is.
     */
    internal fun parsePlaylistPage(html: String): List<Track> {
        val document = runCatching { Jsoup.parse(html) }.getOrNull() ?: return emptyList()

        val blob = document.select("script#__NEXT_DATA__").firstOrNull()?.data()
        if (!blob.isNullOrBlank()) {
            val tracks = runCatching { parseNextData(blob) }.getOrDefault(emptyList())
            if (tracks.isNotEmpty()) return tracks
        }

        // Nothing usable in the blob. The meta tags prove the playlist exists
        // and how big it is, which is worth reporting even without titles.
        return document.select("meta[name=music:song]").mapNotNull {
            it.attr("content").takeIf { url -> url.isNotBlank() }
                ?.let { url -> Track(url.substringAfterLast('/'), "") }
        }
    }

    /**
     * Walk the embedded JSON for anything shaped like a track.
     *
     * Deliberately structural rather than following a fixed path: Spotify
     * reshapes this blob regularly, and a hardcoded path breaks on their
     * schedule. Any object with a name and an artists list is a track, wherever
     * it happens to be nested this month.
     */
    private fun parseNextData(blob: String): List<Track> {
        val json = Json { ignoreUnknownKeys = true }
        val root = json.parseToJsonElement(blob)
        val found = mutableListOf<Track>()

        fun walk(element: kotlinx.serialization.json.JsonElement) {
            when (element) {
                is kotlinx.serialization.json.JsonObject -> {
                    val name = element["name"]?.jsonPrimitive?.contentOrNull
                    val artists = element["artists"]
                    if (!name.isNullOrBlank() && artists != null) {
                        firstArtistName(artists)?.let { found += Track(name, it) }
                    }
                    element.values.forEach(::walk)
                }
                is kotlinx.serialization.json.JsonArray -> element.forEach(::walk)
                else -> Unit
            }
        }

        walk(root)
        return found.distinctBy { it.title.lowercase() to it.artist.lowercase() }
    }

    /**
     * The first artist name, from either shape Spotify uses.
     *
     * `artists` is sometimes `{"items":[…]}` and sometimes a bare array, and
     * each entry carries its name either directly or under `profile`. Branching
     * on the actual type rather than catching the cast failure, because a
     * runCatching around the whole expression swallows the first shape's
     * failure *and* skips the fallback — which is how this only ever parsed
     * Spotify's current layout and silently returned nothing for the other.
     */
    private fun firstArtistName(artists: kotlinx.serialization.json.JsonElement): String? {
        val list = when (artists) {
            is kotlinx.serialization.json.JsonArray -> artists
            is kotlinx.serialization.json.JsonObject ->
                artists["items"] as? kotlinx.serialization.json.JsonArray
            else -> null
        } ?: return null

        val entry = list.firstOrNull() as? kotlinx.serialization.json.JsonObject ?: return null
        val profile = entry["profile"] as? kotlinx.serialization.json.JsonObject
        return profile?.get("name")?.jsonPrimitive?.contentOrNull
            ?: entry["name"]?.jsonPrimitive?.contentOrNull
    }

    /**
     * Read an Exportify CSV.
     *
     * The way around Spotify's 100-track cap, and the reason it is supported:
     * Exportify is what people already use, so accepting its output costs one
     * parser and removes the limitation entirely.
     */
    suspend fun exportifyCsv(context: Context, uri: Uri): List<Track> = withContext(Dispatchers.IO) {
        val input = context.contentResolver.openInputStream(uri) ?: return@withContext emptyList()

        input.use {
            val reader = BufferedReader(InputStreamReader(it))
            val header = reader.readLine()?.split(',')?.map { column -> column.trim('"').lowercase() }
                ?: return@withContext emptyList()

            val titleColumn = header.indexOfFirst { column -> "track name" in column }
            val artistColumn = header.indexOfFirst { column -> "artist name" in column }
            if (titleColumn < 0) return@withContext emptyList()

            reader.lineSequence().mapNotNull { line ->
                val cells = splitCsv(line)
                val title = cells.getOrNull(titleColumn)?.trim() ?: return@mapNotNull null
                if (title.isBlank()) return@mapNotNull null
                Track(title, cells.getOrNull(artistColumn)?.trim().orEmpty())
            }.toList()
        }
    }

    /** A CSV line, respecting quoted commas — song titles are full of them. */
    private fun splitCsv(line: String): List<String> {
        val cells = mutableListOf<String>()
        val current = StringBuilder()
        var quoted = false

        line.forEach { character ->
            when {
                character == '"' -> quoted = !quoted
                character == ',' && !quoted -> {
                    cells += current.toString()
                    current.clear()
                }
                else -> current.append(character)
            }
        }
        cells += current.toString()
        return cells
    }
}
