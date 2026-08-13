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
import dev.rose.bouquet.player.playYouTube
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

    var playback by remember(video.videoId) {
        mutableStateOf<dev.rose.bouquet.youtube.VideoPlayback?>(null)
    }
    var failed by remember(video.videoId) { mutableStateOf(false) }
    var fullscreen by remember { mutableStateOf(false) }

    LaunchedEffect(video.videoId) {
        model.player.let { if (model.playback.value.playing) it.playPause() }
        val found = YouTubeSource.videoPlayback("https://www.youtube.com/watch?v=${video.videoId}")
        if (found == null) failed = true else playback = found
    }

    val exo = remember {
        ExoPlayer.Builder(context).build().apply { playWhenReady = true }
    }

    LaunchedEffect(playback) {
        playback?.let { exo.playYouTube(context, it) }
    }

    // Fullscreen rotates the device and hides the system bars, which is what
    // fullscreen means on a phone — a video filling a portrait window is not
    // it. Both are undone on the way out, including if the screen is left by
    // the back gesture rather than the button.
    val activity = context as? android.app.Activity
    DisposableEffect(fullscreen) {
        val window = activity?.window
        if (window != null) {
            val controller = androidx.core.view.WindowCompat.getInsetsController(
                window, window.decorView)
            if (fullscreen) {
                controller.hide(androidx.core.view.WindowInsetsCompat.Type.systemBars())
                controller.systemBarsBehavior = androidx.core.view.WindowInsetsControllerCompat
                    .BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                activity.requestedOrientation =
                    android.content.pm.ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
            } else {
                controller.show(androidx.core.view.WindowInsetsCompat.Type.systemBars())
                activity.requestedOrientation =
                    android.content.pm.ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
            }
        }
        onDispose {
            val w = activity?.window ?: return@onDispose
            androidx.core.view.WindowCompat.getInsetsController(w, w.decorView)
                .show(androidx.core.view.WindowInsetsCompat.Type.systemBars())
            activity.requestedOrientation =
                android.content.pm.ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
    }

    androidx.activity.compose.BackHandler(enabled = fullscreen) { fullscreen = false }

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
            if (fullscreen) Modifier.fillMaxSize().background(Color.Black)
            else Modifier.fillMaxWidth().aspectRatio(16f / 9f).background(Color.Black),
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
                playback == null -> CircularProgressIndicator(color = theme.accent)
                else -> AndroidView(
                    factory = {
                        PlayerView(it).apply {
                            player = exo
                            useController = true
                            setShowNextButton(false)
                            setShowPreviousButton(false)
                            setFullscreenButtonClickListener { fullscreen = it }
                        }
                    },
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }

        if (fullscreen) return@Column

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
