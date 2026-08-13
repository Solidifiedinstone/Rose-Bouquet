package dev.rose.bouquet.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
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
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.util.UnstableApi
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
 * Browse: music and channels near what you already listen to.
 *
 * The distinct job from Watch is *reach*. Watch is built to be right; this is
 * built to be new — it searches outward from your topics rather than inward
 * from your subscriptions, so it is where a channel you have never seen turns
 * up.
 */
@UnstableApi
@Composable
fun BrowseScreen(model: AppViewModel) {
    val theme = LocalRoseTheme.current
    var shelves by remember { mutableStateOf<List<Pair<String, List<Video>>>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        if (shelves.isEmpty()) {
            loading = true
            shelves = model.browse()
            loading = false
        }
    }

    Column(Modifier.fillMaxSize()) {
        SectionHeading("Browse music") {
            Icon(
                Icons.Default.Refresh, contentDescription = "Look again",
                tint = if (loading) theme.textDim else theme.accent,
                modifier = Modifier.size(22.dp).clickable(enabled = !loading) {
                    loading = true
                },
            )
        }
        LoadingLine(loading)

        if (shelves.isEmpty() && !loading) {
            Empty(
                "Nothing to browse yet",
                "Browse looks for music by the artists in your library. Connect a server " +
                    "and scan it, and this fills in.",
            )
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                shelves.forEach { (heading, items) ->
                    item(key = heading) {
                        Text(
                            heading,
                            style = MaterialTheme.typography.titleSmall,
                            color = theme.text,
                            modifier = Modifier.padding(start = 16.dp, top = 16.dp, bottom = 8.dp),
                        )
                    }
                    item(key = "$heading-row") {
                        // A shelf per reason, scrolled sideways — the shape the
                        // desktop app uses, and the shape a music library wants:
                        // several small groups you can skim, not one long list.
                        LazyRow(
                            contentPadding = androidx.compose.foundation.layout.PaddingValues(
                                horizontal = 12.dp),
                            horizontalArrangement = Arrangement.spacedBy(4.dp),
                        ) {
                            items(items, key = { it.id }) { track ->
                                MusicTile(
                                    track = track,
                                    onPlay = { model.playYouTubeAudio(track) },
                                    onDownload = { model.downloadYouTubeAudio(track) },
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * One track on a Browse shelf.
 *
 * Tapping plays it as audio rather than opening a video, because this is the
 * music tab — the whole distinction from Watch is that nothing here is
 * something you sit and look at.
 */
@Composable
private fun MusicTile(track: Video, onPlay: () -> Unit, onDownload: () -> Unit) {
    val theme = LocalRoseTheme.current
    Column(
        Modifier
            .width(150.dp)
            .clickable(onClick = onPlay)
            .padding(8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Cover(track.thumbnail, Modifier.fillMaxWidth().aspectRatio(1f), corner = 10)
        Text(
            track.title,
            style = MaterialTheme.typography.bodySmall,
            color = theme.text,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                track.channel,
                style = MaterialTheme.typography.labelSmall,
                color = theme.textDim,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Icon(
                Icons.Default.Download,
                contentDescription = "Download",
                tint = theme.textDim,
                modifier = Modifier.size(18.dp).clickable(onClick = onDownload),
            )
        }
    }
}

/**
 * YouTube Music: search it, play it as audio, or download it.
 *
 * Audio-only by design — this is the tab for treating YouTube as a music
 * library rather than as video, so it takes the best audio stream and hands it
 * to the same player everything else uses.
 */
@UnstableApi
@Composable
fun YouTubeMusicScreen(model: AppViewModel) {
    val theme = LocalRoseTheme.current
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<Video>>(emptyList()) }
    var searching by remember { mutableStateOf(false) }

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

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("Search YouTube Music") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(16.dp),
        )
        LoadingLine(searching)

        if (results.isEmpty()) {
            Empty(
                "YouTube Music",
                "Search for anything and play it as audio, or download it into your " +
                    "library. Nothing here needs an account.",
            )
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(results, key = { it.id }) { video ->
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Cover(video.thumbnail, Modifier.size(56.dp))
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(video.title, color = theme.text,
                                style = MaterialTheme.typography.bodyMedium,
                                maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Text(video.channel, color = theme.textDim,
                                style = MaterialTheme.typography.bodySmall)
                        }
                        Icon(
                            Icons.Default.PlayArrow, "Play as audio", tint = theme.accent,
                            modifier = Modifier.size(28.dp).clickable {
                                model.playYouTubeAudio(video)
                            },
                        )
                        Spacer(Modifier.width(10.dp))
                        Icon(
                            Icons.Default.Download, "Download the audio", tint = theme.textDim,
                            modifier = Modifier.size(24.dp).clickable {
                                model.downloadYouTubeAudio(video)
                            },
                        )
                    }
                }
            }
        }
    }
}

private fun Video.asFeedItem(reason: String) = dev.rose.bouquet.data.db.FeedEntity(
    videoId = id, title = title, channel = channel, channelId = channelId,
    thumbnail = thumbnail, durationSeconds = durationSeconds, viewCount = viewCount,
    uploaded = uploaded, reason = reason, score = 0.0, rank = 0, isShort = isShort, builtAt = 0,
)
