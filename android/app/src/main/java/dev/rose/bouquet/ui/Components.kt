package dev.rose.bouquet.ui

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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.SubcomposeAsyncImage
import dev.rose.bouquet.data.db.SongEntity
import dev.rose.bouquet.ui.theme.LocalRoseTheme

/**
 * Cover art, with something deliberate drawn while it is not there.
 *
 * The placeholder is a themed panel rather than a spinner or a blank: a grid of
 * spinners reads as broken, and a grid of blanks reads as empty. A tinted tile
 * with a note on it reads as "art is coming", which is the truth.
 */
@Composable
fun Cover(
    url: String?,
    modifier: Modifier = Modifier,
    corner: Int = 8,
) {
    val theme = LocalRoseTheme.current
    val shape = RoundedCornerShape(corner.dp)

    Box(modifier.clip(shape).background(theme.placeholder)) {
        if (url == null) {
            Placeholder()
        } else {
            SubcomposeAsyncImage(
                model = url,
                contentDescription = null,
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
                loading = { Placeholder() },
                error = { Placeholder() },
            )
        }
    }
}

@Composable
private fun Placeholder() {
    val theme = LocalRoseTheme.current
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Icon(
            Icons.Default.MusicNote,
            contentDescription = null,
            tint = theme.textDim.copy(alpha = 0.4f),
            modifier = Modifier.size(28.dp),
        )
    }
}

/** One song in a list. */
@Composable
fun SongRow(
    song: SongEntity,
    coverUrl: String?,
    playing: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    onDownload: (() -> Unit)? = null,
    trailing: @Composable (() -> Unit)? = null,
) {
    val theme = LocalRoseTheme.current
    Row(
        modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Cover(coverUrl, Modifier.size(48.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                song.title,
                style = MaterialTheme.typography.bodyLarge,
                // The playing track is marked with colour rather than an icon
                // in the row: an icon shifts the text and makes the whole list
                // twitch every time the track changes.
                color = if (playing) theme.accent else theme.text,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Text(
                song.artist,
                style = MaterialTheme.typography.bodySmall,
                color = theme.textDim,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        Text(
            song.durationSeconds.asClock(),
            style = MaterialTheme.typography.labelMedium,
            color = theme.textDim,
        )
        // A download control on every row, not only on the album header. It is
        // the second most common thing anybody does with a track, and burying
        // it meant the only way to keep one song was to download its album.
        onDownload?.let {
            Spacer(Modifier.width(10.dp))
            Icon(
                if (song.downloaded) Icons.Default.DownloadDone else Icons.Default.Download,
                contentDescription = if (song.downloaded) "Downloaded — tap to remove"
                else "Download for offline",
                tint = if (song.downloaded) theme.success else theme.textDim,
                modifier = Modifier.size(22.dp).clickable(onClick = it),
            )
        }
        trailing?.let { Spacer(Modifier.width(4.dp)); it() }
    }
}

/** A tile for an album or a channel. */
@Composable
fun CoverTile(
    title: String,
    subtitle: String,
    coverUrl: String?,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val theme = LocalRoseTheme.current
    Column(
        modifier
            .clickable(onClick = onClick)
            .padding(8.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Cover(coverUrl, Modifier.fillMaxWidth().aspectRatio(1f), corner = 10)
        Text(
            title,
            style = MaterialTheme.typography.bodyMedium,
            color = theme.text,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            subtitle,
            style = MaterialTheme.typography.bodySmall,
            color = theme.textDim,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/**
 * What a screen shows when it has nothing.
 *
 * Always says what would fill it and how, because "no results" on its own is
 * indistinguishable from a bug — and half the time it *is* one.
 */
@Composable
fun Empty(title: String, detail: String, modifier: Modifier = Modifier) {
    val theme = LocalRoseTheme.current
    Column(
        modifier.fillMaxSize().padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(title, style = MaterialTheme.typography.titleMedium, color = theme.text)
        Spacer(Modifier.size(8.dp))
        Text(
            detail,
            style = MaterialTheme.typography.bodyMedium,
            color = theme.textDim,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
    }
}

/** Seconds as `m:ss`, or `h:mm:ss` past an hour. */
fun Int.asClock(): String {
    if (this <= 0) return "—"
    val hours = this / 3600
    val minutes = (this % 3600) / 60
    val seconds = this % 60
    return if (hours > 0) "%d:%02d:%02d".format(hours, minutes, seconds)
    else "%d:%02d".format(minutes, seconds)
}

fun Long.asClock(): String = toInt().asClock()

/** Large numbers the way a person would say them. */
fun Long.asCount(): String = when {
    this >= 1_000_000_000 -> "%.1fB".format(this / 1_000_000_000.0)
    this >= 1_000_000 -> "%.1fM".format(this / 1_000_000.0)
    this >= 1_000 -> "%.1fK".format(this / 1_000.0)
    else -> toString()
}
