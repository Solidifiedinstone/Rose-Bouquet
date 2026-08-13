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
import androidx.compose.foundation.layout.displayCutoutPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
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

    // A Dialog, because this screen is drawn inside the app's Scaffold — with a
    // top bar above it and the nav bar and mini player below. Filling "the
    // screen" from in there only ever filled the gap between them, which is
    // why fullscreen was not fullscreen. A dialog with the platform width
    // disabled is a surface over the whole window, bars included.
    if (fullscreen) {
        androidx.compose.ui.window.Dialog(
            onDismissRequest = { fullscreen = false },
            properties = androidx.compose.ui.window.DialogProperties(
                usePlatformDefaultWidth = false,
                dismissOnBackPress = true,
                dismissOnClickOutside = false,
            ),
        ) {
            // The dialog is its own window, so the bars have to be hidden on
            // *it* — hiding them on the activity's window leaves the status bar
            // sitting over the video, which is most of what "not fullscreen"
            // looked like.
            val dialogWindow = (androidx.compose.ui.platform.LocalView.current.parent
                as? androidx.compose.ui.window.DialogWindowProvider)?.window
            androidx.compose.runtime.SideEffect {
                dialogWindow?.let { window ->
                    androidx.core.view.WindowCompat.setDecorFitsSystemWindows(window, false)
                    window.setLayout(
                        android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                        android.view.ViewGroup.LayoutParams.MATCH_PARENT,
                    )
                    androidx.core.view.WindowCompat.getInsetsController(window, window.decorView)
                        .apply {
                            hide(androidx.core.view.WindowInsetsCompat.Type.systemBars())
                            systemBarsBehavior = androidx.core.view.WindowInsetsControllerCompat
                                .BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                        }
                }
            }

            Box(Modifier.fillMaxSize().background(Color.Black), contentAlignment = Alignment.Center) {
                AndroidView(
                    factory = {
                        PlayerView(it).apply {
                            useController = true
                            setShowNextButton(false)
                            setShowPreviousButton(false)
                            // Fills the screen rather than sitting in a letterbox.
                            // A phone is taller than 16:9, so fitting leaves bars
                            // down both sides in landscape, which is the thing
                            // that did not feel like fullscreen.
                            resizeMode =
                                androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_ZOOM
                            setFullscreenButtonClickListener { fullscreen = false }
                        }
                    },
                    update = { it.player = exo },
                    modifier = Modifier.fillMaxSize(),
                )

                // An exit that is always on screen.
                //
                // The player's own fullscreen button is part of its controller,
                // and a controller hides itself a few seconds after the last
                // touch — so for most of a video there was nothing to leave
                // with, and the only way out was a back gesture the system bars
                // were hidden for. A control you have to tap the screen to
                // summon is not one you can find when you do not know it is
                // there.
                Box(
                    Modifier
                        .align(Alignment.TopEnd)
                        // Clear of the notch: the system bars are hidden here,
                        // so nothing else keeps this out from under a cutout.
                        .displayCutoutPadding()
                        .padding(16.dp)
                        .size(44.dp)
                        .background(Color.Black.copy(alpha = 0.55f), CircleShape)
                        .clickable { fullscreen = false },
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        Icons.Default.Close,
                        contentDescription = "Leave fullscreen",
                        tint = Color.White,
                        modifier = Modifier.size(24.dp),
                    )
                }
            }
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
            Modifier.fillMaxWidth().aspectRatio(16f / 9f).background(Color.Black),
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
                // Nothing is attached here while fullscreen: a player can only
                // render to one view, and leaving this one attached means the
                // fullscreen surface stays black.
                !fullscreen -> AndroidView(
                    factory = {
                        PlayerView(it).apply {
                            useController = true
                            setShowNextButton(false)
                            setShowPreviousButton(false)
                            setFullscreenButtonClickListener { fullscreen = true }
                        }
                    },
                    update = { it.player = if (fullscreen) null else exo },
                    modifier = Modifier.fillMaxSize(),
                )

                else -> Unit
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
