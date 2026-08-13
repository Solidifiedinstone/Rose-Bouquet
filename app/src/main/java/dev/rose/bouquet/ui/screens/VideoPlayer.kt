package dev.rose.bouquet.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import dev.rose.bouquet.data.db.FeedEntity
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.ui.theme.LocalRoseTheme
import dev.rose.bouquet.youtube.YouTubeSource

/**
 * Plays one video.
 *
 * Its own short-lived [ExoPlayer] rather than the shared music service, which
 * looks like duplication and is not: the music player is a background service
 * with a notification and a queue that survives the screen, and a video is the
 * opposite of all three. Sharing one would mean a video appearing in the music
 * queue and the notification claiming to play something with a picture.
 *
 * Starting a video pauses the music rather than playing both at once — the
 * same rule the desktop app follows.
 */
@UnstableApi
@Composable
fun VideoPlayer(model: AppViewModel, video: FeedEntity, onBack: () -> Unit) {
    val context = LocalContext.current
    val theme = LocalRoseTheme.current

    var streamUrl by remember(video.videoId) { mutableStateOf<String?>(null) }
    var failed by remember(video.videoId) { mutableStateOf(false) }

    LaunchedEffect(video.videoId) {
        model.player.let { if (model.playback.value.playing) it.playPause() }
        val playable = YouTubeSource.videoStream("https://www.youtube.com/watch?v=${video.videoId}")
        if (playable == null) failed = true else streamUrl = playable.url
    }

    val exo = remember {
        ExoPlayer.Builder(context).build().apply { playWhenReady = true }
    }

    LaunchedEffect(streamUrl) {
        streamUrl?.let {
            exo.setMediaItem(MediaItem.fromUri(it))
            exo.prepare()
        }
    }

    // Record the view when leaving, with how much was actually watched. A view
    // is what every recommendation downstream is built from, so it is recorded
    // once, on the way out, rather than on the way in where a mis-tap counts.
    DisposableEffect(video.videoId) {
        onDispose {
            val completion =
                if (exo.duration > 0) (exo.currentPosition.toFloat() / exo.duration).coerceIn(0f, 1f)
                else 0f
            model.watched(video, completion)
            exo.release()
        }
    }

    Column(Modifier.fillMaxSize().background(theme.background)) {
        Box(
            Modifier
                .fillMaxWidth()
                .aspectRatio(16f / 9f)
                .background(Color.Black),
            contentAlignment = Alignment.Center,
        ) {
            when {
                failed -> Text(
                    "That video would not play.\nYouTube may have changed something.",
                    color = Color.White,
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                    modifier = Modifier.padding(24.dp),
                )
                streamUrl == null -> CircularProgressIndicator(color = theme.accent)
                else -> AndroidView(
                    factory = {
                        PlayerView(it).apply {
                            player = exo
                            useController = true
                            setShowNextButton(false)
                            setShowPreviousButton(false)
                        }
                    },
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }

        Row(Modifier.fillMaxWidth().padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("‹ Back", color = theme.accent, style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.clickable(onClick = onBack))
        }

        Column(Modifier.padding(horizontal = 16.dp)) {
            Text(video.title, style = MaterialTheme.typography.titleMedium, color = theme.text)
            Spacer(Modifier.size(4.dp))
            Text(video.channel, style = MaterialTheme.typography.bodyMedium, color = theme.textDim)
            Spacer(Modifier.size(12.dp))
            Row {
                Text(
                    "Follow ${video.channel}",
                    style = MaterialTheme.typography.labelLarge,
                    color = theme.accent,
                    modifier = Modifier.clickable {
                        video.channelId?.let {
                            model.follow(it, video.channel, "https://www.youtube.com/channel/$it")
                        }
                    },
                )
                Spacer(Modifier.width(20.dp))
                Text(
                    "Not interested",
                    style = MaterialTheme.typography.labelLarge,
                    color = theme.textDim,
                    modifier = Modifier.clickable {
                        model.setOpinion(video, false)
                        onBack()
                    },
                )
            }
        }
    }
}
