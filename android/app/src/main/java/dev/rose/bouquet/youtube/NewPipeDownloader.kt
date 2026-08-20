package dev.rose.bouquet.youtube

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.RequestBody.Companion.toRequestBody
import org.schabi.newpipe.extractor.downloader.Downloader
import org.schabi.newpipe.extractor.downloader.Request
import org.schabi.newpipe.extractor.downloader.Response
import org.schabi.newpipe.extractor.exceptions.ReCaptchaException
import java.util.concurrent.TimeUnit

/**
 * The HTTP side of NewPipeExtractor, on OkHttp.
 *
 * The extractor deliberately does not ship a network layer, so every host
 * supplies one. This is the whole of it: take its [Request], make the call,
 * hand back a [Response].
 *
 * The user agent matters more than it looks. YouTube serves materially
 * different pages to clients it does not recognise, and a missing or odd one is
 * the difference between a parse that works and a parse that returns nothing
 * with no error to explain it.
 */
class NewPipeDownloader(
    private val http: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .build(),
) : Downloader() {

    override fun execute(request: Request): Response {
        val builder = okhttp3.Request.Builder()
            .method(
                request.httpMethod(),
                request.dataToSend()?.toRequestBody(),
            )
            .url(request.url())
            .addHeader("User-Agent", USER_AGENT)

        // Signed in, if a session has been brought over. Only to YouTube and
        // Google: a session is not something to attach to every request an
        // extractor happens to make, and the extractor makes them to image
        // hosts and redirectors too.
        session?.takeIf { it.isNotBlank() && wantsSession(request.url()) }
            ?.let { builder.addHeader("Cookie", it) }

        request.headers().forEach { (name, values) ->
            builder.removeHeader(name)
            values.forEach { builder.addHeader(name, it) }
        }

        val response = http.newCall(builder.build()).execute()

        if (response.code == HTTP_TOO_MANY_REQUESTS) {
            response.close()
            // YouTube has decided this address looks like a robot. Surfacing it
            // as the extractor's own exception lets the screens say something
            // true — that YouTube is rate-limiting, not that the app is broken.
            throw ReCaptchaException("YouTube is asking for a captcha", request.url())
        }

        val body = response.body?.string()
        return Response(
            response.code,
            response.message,
            response.headers.toMultimap(),
            body,
            response.request.url.toString(),
        )
    }

    companion object {
        /**
         * The signed-in session, as a `Cookie:` header, or null for signed out.
         *
         * Held here rather than passed through every call because the
         * extractor builds its own requests several layers down and there is
         * nowhere to thread it through. Set from `YouTubeSession`.
         */
        @Volatile
        @JvmStatic
        var session: String? = null

        /** Whether this URL is one of YouTube's, and so ours to sign. */
        private fun wantsSession(url: String): Boolean {
            val host = url.toHttpUrlOrNull()?.host ?: return false
            return host == "youtube.com" || host.endsWith(".youtube.com") ||
                host == "google.com" || host.endsWith(".google.com")
        }

        private const val HTTP_TOO_MANY_REQUESTS = 429

        /**
         * A current desktop Firefox. Kept deliberately ordinary: the extractor
         * parses the page a browser would get, so claiming to be something
         * unusual is how you end up parsing a page nobody tested against.
         */
        const val USER_AGENT =
            "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0"
    }
}
