package dev.rose.bouquet.player

import android.content.Context
import androidx.media3.common.MediaItem
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.MergingMediaSource
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import dev.rose.bouquet.youtube.NewPipeDownloader
import dev.rose.bouquet.youtube.VideoPlayback

/**
 * Handing a YouTube stream to ExoPlayer.
 *
 * The whole of the awkwardness is that YouTube barely serves progressive
 * streams any more — a typical video offers one at 360p against thirteen
 * video-only streams up to 1440p — so anything watchable arrives as a picture
 * track and a sound track that have to be played together. [MergingMediaSource]
 * is how that is done, and it is why this is not one line at the call site.
 */
@UnstableApi
fun ExoPlayer.playYouTube(context: Context, playback: VideoPlayback) {
    // Google's CDN serves different bytes to clients it does not recognise, so
    // the data source claims the same user agent the extractor used to obtain
    // these URLs. A mismatch here is a 403 on a URL that just worked.
    val factory = DefaultHttpDataSource.Factory()
        .setUserAgent(NewPipeDownloader.USER_AGENT)
        .setAllowCrossProtocolRedirects(true)

    if (playback.audioUrl == null) {
        setMediaSource(
            ProgressiveMediaSource.Factory(factory)
                .createMediaSource(MediaItem.fromUri(playback.videoUrl))
        )
    } else {
        setMediaSource(
            MergingMediaSource(
                ProgressiveMediaSource.Factory(factory)
                    .createMediaSource(MediaItem.fromUri(playback.videoUrl)),
                ProgressiveMediaSource.Factory(factory)
                    .createMediaSource(MediaItem.fromUri(playback.audioUrl)),
            )
        )
    }
    prepare()
}
