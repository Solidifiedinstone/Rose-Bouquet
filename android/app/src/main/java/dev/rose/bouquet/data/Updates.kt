package dev.rose.bouquet.data

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File

/**
 * Checking for, and installing, a new version.
 *
 * There is no Play Store here, so an update is an APK somebody has to fetch and
 * open. That is three manual steps and easy to get wrong; this is the same
 * three steps behind one button.
 *
 * It does not install anything by itself. The APK is downloaded and handed to
 * Android's own installer, which asks the user to confirm — an app that could
 * silently replace itself would be a far worse thing to have on a phone than
 * the inconvenience it saves.
 */
object Updates {

    data class Release(val version: String, val notes: String, val apkUrl: String?)

    /**
     * Where releases come from.
     *
     * The one repository, since the phone client moved into `android/` there
     * and the two halves go out in the same release. Pointing at the old
     * Android-only repository would keep looking at a stream that has stopped
     * — which shows up as "this is the newest version" for ever, the quietest
     * possible way for an updater to be broken.
     */
    private const val LATEST =
        "https://api.github.com/repos/Solidifiedinstone/Rose-Bouquet/releases/latest"

    /**
     * What the newest release is, or null if that cannot be established.
     *
     * Null rather than an exception: a phone with no signal, or a private
     * repository the token cannot see, is not an error worth a dialog.
     */
    suspend fun latest(): Release? = withContext(Dispatchers.IO) {
        val request = Request.Builder().url(LATEST)
            .header("Accept", "application/vnd.github+json")
            .build()

        val body = runCatching {
            OkHttpClient().newCall(request).execute().use { response ->
                if (!response.isSuccessful) null else response.body?.string()
            }
        }.getOrNull() ?: return@withContext null

        runCatching {
            val row = Json { ignoreUnknownKeys = true }.parseToJsonElement(body).jsonObject
            Release(
                version = row["tag_name"]?.jsonPrimitive?.content.orEmpty().removePrefix("v"),
                notes = row["body"]?.jsonPrimitive?.content.orEmpty(),
                apkUrl = row["assets"]?.jsonArray
                    ?.map { it.jsonObject }
                    ?.firstOrNull { it["name"]?.jsonPrimitive?.content?.endsWith(".apk") == true }
                    ?.get("browser_download_url")?.jsonPrimitive?.content,
            )
        }.getOrNull()
    }

    /**
     * Whether [candidate] is actually newer than [current].
     *
     * Compared field by field as numbers. A string comparison calls 0.1.10
     * older than 0.1.9, which is the kind of thing that stops an update
     * appearing exactly when it starts mattering.
     */
    fun isNewer(candidate: String, current: String): Boolean {
        fun parts(value: String) = value.trim().split(".", "-")
            .mapNotNull { it.takeWhile(Char::isDigit).toIntOrNull() }

        val new = parts(candidate)
        val old = parts(current)
        for (i in 0 until maxOf(new.size, old.size)) {
            val a = new.getOrElse(i) { 0 }
            val b = old.getOrElse(i) { 0 }
            if (a != b) return a > b
        }
        return false
    }

    /** Download the APK and hand it to Android's installer. */
    suspend fun download(context: Context, url: String): Intent? = withContext(Dispatchers.IO) {
        val target = File(context.cacheDir, "update.apk")

        val ok = runCatching {
            OkHttpClient().newCall(Request.Builder().url(url).build()).execute().use { response ->
                if (!response.isSuccessful) return@runCatching false
                response.body?.byteStream()?.use { input ->
                    target.outputStream().use { output -> input.copyTo(output) }
                }
                true
            }
        }.getOrDefault(false)

        if (!ok || target.length() == 0L) return@withContext null

        // A content:// URI through FileProvider — a file:// one throws
        // FileUriExposedException on anything since Nougat.
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.updates", target)
        Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
    }
}
