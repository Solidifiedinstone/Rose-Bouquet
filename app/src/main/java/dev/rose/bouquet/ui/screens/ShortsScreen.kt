package dev.rose.bouquet.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.pager.VerticalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import dev.rose.bouquet.data.db.FeedEntity
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.player.playYouTube
import dev.rose.bouquet.ui.Cover
import dev.rose.bouquet.ui.Empty
import dev.rose.bouquet.youtube.YouTubeSource

/**
 * The Shorts reel: swipe up, next short.
 *
 * A [VerticalPager] rather than a list, because the interaction is
 * one-at-a-time and snapping is the whole feel of it. The important part is
 * what it *does not* do: only the page in view holds a player. The desktop
 * app's first attempt kept every card alive and a scroll cost 400 ms; here
 * exactly one [ExoPlayer] exists, and it follows the current page.
 *
 * Views recorded from here are marked as shorts, so a long doomscroll never
 * rewrites what the Watch tab thinks you like.
 */
@UnstableApi
@Composable
fun ShortsScreen(model: AppViewModel) {
    val feed by model.shortsFeed.collectAsStateWithLifecycle()
    val building by model.buildingFeed.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) {
        if (feed.isEmpty()) model.buildFeed(shorts = true)
    }

    if (feed.isEmpty()) {
        Empty(
            if (building) "Finding shorts…" else "No shorts yet",
            if (building) "Reading the channels you watch."
            else "Shorts are built from what you watch. Watch a few things and this fills in.",
        )
        return
    }

    val pager = rememberPagerState(pageCount = { feed.size })
    val context = LocalContext.current

    // One player for the whole reel, reused as pages change. Creating one per
    // page is what makes a reel stutter: each costs a codec handle, and the
    // device has only a handful.
    val exo = remember {
        ExoPlayer.Builder(context).build().apply {
            repeatMode = Player.REPEAT_MODE_ONE      // shorts loop
            playWhenReady = true
        }
    }
    DisposableEffect(Unit) { onDispose { exo.release() } }

    var loading by remember { mutableStateOf(true) }

    // Follow the settled page rather than every frame of the swipe, so a fling
    // through ten shorts resolves one stream instead of ten.
    LaunchedEffect(pager) {
        snapshotFlow { pager.settledPage }.collect { page ->
            val short = feed.getOrNull(page) ?: return@collect
            loading = true
            exo.stop()
            val found = YouTubeSource.videoPlayback(
                "https://www.youtube.com/shorts/${short.videoId}", maxHeight = 720)
            if (found != null) exo.playYouTube(context, found)
            loading = false
            model.watched(short)
        }
    }

    VerticalPager(state = pager, modifier = Modifier.fillMaxSize()) { page ->
        val short = feed[page]
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            if (page == pager.settledPage) {
                AndroidView(
                    factory = {
                        PlayerView(it).apply {
                            player = exo
                            useController = false
                            // Fill the screen the way every other reel does;
                            // letterboxing a vertical video looks broken.
                            resizeMode =
                                androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_ZOOM
                        }
                    },
                    modifier = Modifier.fillMaxSize(),
                )
                if (loading) {
                    CircularProgressIndicator(
                        color = Color.White,
                        modifier = Modifier.align(Alignment.Center),
                    )
                }
            } else {
                // Off-screen pages show the thumbnail only — no decoder, no cost.
                Cover(short.thumbnail, Modifier.fillMaxSize(), corner = 0)
            }

            Overlay(short, model, Modifier.align(Alignment.BottomStart))
        }
    }
}

@UnstableApi
@Composable
private fun Overlay(short: FeedEntity, model: AppViewModel, modifier: Modifier = Modifier) {
    Column(
        modifier
            .fillMaxWidth()
            .background(Color.Black.copy(alpha = 0.45f))
            .padding(16.dp),
    ) {
        Text(short.title, color = Color.White, style = MaterialTheme.typography.bodyLarge,
            maxLines = 2, overflow = TextOverflow.Ellipsis)
        Spacer(Modifier.size(4.dp))
        Text(short.channel, color = Color.White.copy(alpha = 0.8f),
            style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.size(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Default.ThumbUp, "Like", tint = Color.White,
                modifier = Modifier.size(22.dp).clickable { model.setOpinion(short, true) })
            Spacer(Modifier.width(22.dp))
            Icon(Icons.Default.ThumbDown, "Not interested", tint = Color.White,
                modifier = Modifier.size(22.dp).clickable { model.setOpinion(short, false) })
            Spacer(Modifier.width(22.dp))
            short.channelId?.let { id ->
                Text("Follow", color = Color.White, style = MaterialTheme.typography.labelLarge,
                    modifier = Modifier.clickable {
                        model.follow(id, short.channel, "https://www.youtube.com/channel/$id")
                    })
            }
        }
    }
}
