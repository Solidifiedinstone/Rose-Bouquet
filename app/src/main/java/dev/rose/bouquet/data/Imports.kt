package dev.rose.bouquet.data

import android.content.Context
import android.net.Uri
import dev.rose.bouquet.data.db.RoseDatabase
import dev.rose.bouquet.data.db.WatchEntity
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
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

        val stream = context.contentResolver.openInputStream(uri)
            ?: return@withContext Result(0, 0, "Could not open that file")

        stream.use { input ->
            ZipInputStream(input).use { zip ->
                while (true) {
                    val entry = zip.nextEntry ?: break
                    val name = entry.name.lowercase()
                    if (!name.contains("watch-history")) continue

                    val text = zip.readBytes().decodeToString()
                    val records = if (name.endsWith(".json")) parseTakeoutJson(text)
                    else parseTakeoutHtml(text)

                    records.forEach { record ->
                        if (record.videoId in known) {
                            skipped++
                        } else {
                            known += record.videoId
                            youtube.watched(record)
                            added++
                        }
                    }
                }
            }
        }

        Result(
            added, skipped,
            when {
                added == 0 && skipped == 0 ->
                    "No watch history in that archive. Ask Takeout for YouTube history " +
                        "specifically — a Takeout of everything else has none."
                added == 0 -> "Already imported — all $skipped were known."
                else -> "Imported $added, skipped $skipped already known."
            },
        )
    }

    private fun parseTakeoutJson(text: String): List<WatchEntity> {
        val json = Json { ignoreUnknownKeys = true }
        val array = runCatching { json.parseToJsonElement(text).jsonArray }.getOrNull()
            ?: return emptyList()

        return array.mapNotNull { element ->
            val row = element.jsonObject
            val url = row["titleUrl"]?.jsonPrimitive?.content ?: return@mapNotNull null
            val videoId = url.substringAfter("v=", "").substringBefore('&')
            if (videoId.isBlank()) return@mapNotNull null

            val title = row["title"]?.jsonPrimitive?.content.orEmpty()
                .removePrefix("Watched ")
            val channel = row["subtitles"]?.jsonArray?.firstOrNull()
                ?.jsonObject?.get("name")?.jsonPrimitive?.content.orEmpty()
            val channelUrl = row["subtitles"]?.jsonArray?.firstOrNull()
                ?.jsonObject?.get("url")?.jsonPrimitive?.content

            WatchEntity(
                videoId = videoId,
                title = title,
                channel = channel,
                channelId = channelUrl?.substringAfterLast('/'),
                isShort = "/shorts/" in url,
                watchedAt = parseTime(row["time"]?.jsonPrimitive?.content),
            )
        }
    }

    /**
     * The HTML export.
     *
     * Parsed with Jsoup rather than by regex because it is real, messy HTML —
     * and because Jsoup is already here as a NewPipeExtractor dependency, so it
     * costs nothing.
     */
    private fun parseTakeoutHtml(text: String): List<WatchEntity> {
        val document = runCatching { Jsoup.parse(text) }.getOrNull() ?: return emptyList()

        return document.select("div.content-cell").mapNotNull { cell ->
            val links = cell.select("a")
            val videoLink = links.firstOrNull { "watch?v=" in it.attr("href") || "/shorts/" in it.attr("href") }
                ?: return@mapNotNull null
            val href = videoLink.attr("href")
            val videoId = href.substringAfter("v=", "").substringBefore('&')
                .ifBlank { href.substringAfterLast('/') }
            if (videoId.isBlank()) return@mapNotNull null

            val channelLink = links.firstOrNull { "/channel/" in it.attr("href") }

            WatchEntity(
                videoId = videoId,
                // Jsoup unescapes entities, which the desktop app originally
                // forgot and ended up with "Rock &amp; Roll" in its history.
                title = videoLink.text(),
                channel = channelLink?.text().orEmpty(),
                channelId = channelLink?.attr("href")?.substringAfterLast('/'),
                isShort = "/shorts/" in href,
                watchedAt = parseTime(cell.ownText()),
            )
        }
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

        // The track list is embedded in the page as JSON-LD.
        val document = Jsoup.parse(body)
        document.select("meta[name=music:song]").mapNotNull { meta ->
            val songUrl = meta.attr("content")
            songUrl.takeIf { it.isNotBlank() }?.let { Track(it.substringAfterLast('/'), "") }
        }
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
