package dev.rose.bouquet.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.util.UnstableApi
import androidx.navigation.NavController
import dev.rose.bouquet.data.db.FeedEntity
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.ui.Cover
import dev.rose.bouquet.ui.Empty
import dev.rose.bouquet.ui.LoadingLine
import dev.rose.bouquet.ui.SectionHeading
import dev.rose.bouquet.ui.asClock
import dev.rose.bouquet.ui.asCount
import dev.rose.bouquet.ui.theme.LocalRoseTheme
import dev.rose.bouquet.youtube.Video

/**
 * The Watch tab: a feed built here, and a search box.
 *
 * Every row carries the reason it is there. That is not decoration — it is the
 * difference between an algorithm you can correct and one you can only
 * tolerate. If a row says "Because you watch about steam presses" and that is
 * wrong, the fix is visible from the row.
 */
@UnstableApi
@Composable
fun WatchScreen(model: AppViewModel, navController: NavController) {
    val feed by model.watchFeed.collectAsStateWithLifecycle()
    val building by model.buildingFeed.collectAsStateWithLifecycle()
    val theme = LocalRoseTheme.current

    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<Video>>(emptyList()) }
    var searching by remember { mutableStateOf(false) }
    var playing by remember { mutableStateOf<FeedEntity?>(null) }

    // Build once if there is nothing stored. Not on every visit: the feed is
    // persisted precisely so returning to this tab is instant.
    LaunchedEffect(Unit) {
        if (feed.isEmpty()) model.buildFeed(shorts = false)
    }

    LaunchedEffect(query) {
        if (query.isBlank()) {
            results = emptyList()
            return@LaunchedEffect
        }
        kotlinx.coroutines.delay(400)
        searching = true
        results = model.searchYouTube(query)
        searching = false
    }

    playing?.let { video ->
        VideoPlayer(model, video) { playing = null }
        return
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("Search YouTube") },
            leadingIcon = { Icon(Icons.Default.Search, contentDescription = null) },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        )
        LoadingLine(building || searching)

        when {
            query.isNotBlank() -> LazyColumn(Modifier.fillMaxSize()) {
                items(results, key = { it.id }) { video ->
                    VideoRow(
                        title = video.title,
                        channel = video.channel,
                        reason = "${video.viewCount.asCount()} views",
                        thumbnail = video.thumbnail,
                        duration = video.durationSeconds,
                        onClick = {
                            playing = FeedEntity(
                                videoId = video.id, title = video.title, channel = video.channel,
                                channelId = video.channelId, thumbnail = video.thumbnail,
                                durationSeconds = video.durationSeconds,
                                viewCount = video.viewCount, uploaded = video.uploaded,
                                reason = "From search", score = 0.0, rank = 0,
                                isShort = false, builtAt = 0,
                            )
                        },
                    )
                }
            }

            feed.isEmpty() && building -> Empty("Building your feed…", "Reading the channels you watch.")

            feed.isEmpty() -> Empty(
                "Nothing to watch yet",
                "The feed is built from what you actually watch, so it starts empty. " +
                    "Search for something, watch it, and this fills in.",
            )

            else -> LazyColumn(Modifier.fillMaxSize()) {
                item {
                    SectionHeading("For you") {
                        Icon(
                            Icons.Default.Refresh,
                            contentDescription = "Rebuild the feed",
                            tint = if (building) theme.textDim else theme.accent,
                            modifier = Modifier.size(22.dp)
                                .clickable(enabled = !building) { model.buildFeed(shorts = false) },
                        )
                    }
                }
                items(feed, key = { it.videoId }) { video ->
                    VideoRow(
                        title = video.title,
                        channel = video.channel,
                        reason = video.reason,
                        thumbnail = video.thumbnail,
                        duration = video.durationSeconds,
                        onClick = { playing = video },
                        onLike = { model.setOpinion(video, true) },
                        onDislike = { model.setOpinion(video, false) },
                    )
                }
            }
        }
    }
}

@Composable
private fun VideoRow(
    title: String,
    channel: String,
    reason: String,
    thumbnail: String?,
    duration: Long,
    onClick: () -> Unit,
    onLike: (() -> Unit)? = null,
    onDislike: (() -> Unit)? = null,
) {
    val theme = LocalRoseTheme.current
    Column(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
    ) {
        Box {
            Cover(
                thumbnail,
                Modifier.fillMaxWidth().aspectRatio(16f / 9f),
                corner = 10,
            )
            if (duration > 0) {
                Text(
                    duration.asClock(),
                    style = MaterialTheme.typography.labelSmall,
                    color = androidx.compose.ui.graphics.Color.White,
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .padding(6.dp)
                        .clip(RoundedCornerShape(4.dp))
                        .background(androidx.compose.ui.graphics.Color.Black.copy(alpha = 0.75f))
                        .padding(horizontal = 5.dp, vertical = 2.dp),
                )
            }
        }
        Spacer(Modifier.size(8.dp))
        Text(title, style = MaterialTheme.typography.bodyLarge, color = theme.text,
            maxLines = 2, overflow = TextOverflow.Ellipsis)
        Text(channel, style = MaterialTheme.typography.bodySmall, color = theme.textDim,
            maxLines = 1, overflow = TextOverflow.Ellipsis)

        Row(verticalAlignment = Alignment.CenterVertically) {
            // The reason this is on your screen, in the app's own words.
            Text(
                reason,
                style = MaterialTheme.typography.labelSmall,
                color = theme.accentMuted,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            if (onLike != null && onDislike != null) {
                Icon(Icons.Default.ThumbUp, "Like", tint = theme.textDim,
                    modifier = Modifier.size(18.dp).clickable(onClick = onLike))
                Spacer(Modifier.width(14.dp))
                Icon(Icons.Default.ThumbDown, "Not interested", tint = theme.textDim,
                    modifier = Modifier.size(18.dp).clickable(onClick = onDislike))
            }
        }
    }
}
