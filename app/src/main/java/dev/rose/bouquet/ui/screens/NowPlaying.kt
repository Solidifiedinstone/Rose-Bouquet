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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Repeat
import androidx.compose.material.icons.filled.RepeatOne
import androidx.compose.material.icons.filled.Shuffle
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.SkipPrevious
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.ui.Cover
import dev.rose.bouquet.ui.asClock
import dev.rose.bouquet.ui.theme.LocalRoseTheme

/**
 * The full transport, as a sheet over whatever screen you were on.
 *
 * A sheet rather than a route so it never becomes a place you have to navigate
 * *back* out of — the queue and the library it came from are still underneath.
 */
@OptIn(ExperimentalMaterial3Api::class)
@UnstableApi
@Composable
fun NowPlayingSheet(model: AppViewModel, onDismiss: () -> Unit) {
    val playback by model.playback.collectAsStateWithLifecycle()
    val theme = LocalRoseTheme.current
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    val song = playback.song

    // Position emits no events of its own, so it is polled — but only while
    // this sheet is on screen. A ticker running behind a closed sheet is a
    // wakeup per second for a number nobody is looking at.
    var scrubbing by remember { mutableStateOf<Float?>(null) }
    var showVisualiser by remember { mutableStateOf(false) }
    val settings by model.settings.collectAsStateWithLifecycle()

    // Started only while it is on screen and switched on. An audio tap running
    // behind a closed sheet is a wakeup per frame for something nobody can see.
    val spectrum = remember { dev.rose.bouquet.player.Spectrum() }
    DisposableEffect(showVisualiser) {
        if (showVisualiser) spectrum.start()
        onDispose { spectrum.stop() }
    }
    LaunchedEffect(Unit) {
        while (true) {
            model.player.tick()
            kotlinx.coroutines.delay(500)
        }
    }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = theme.surface,
    ) {
        Column(
            Modifier.fillMaxWidth().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // The visualiser lives here, in place of the artwork, because
            // this is the screen somebody is looking at while music plays —
            // the Visualiser tab is for setting it up, not for watching it.
            Box(
                Modifier.fillMaxWidth(0.8f).aspectRatio(1f),
                contentAlignment = Alignment.Center,
            ) {
                if (showVisualiser) {
                    Box(
                        Modifier.fillMaxSize()
                            .clip(RoundedCornerShape(16.dp))
                            .background(theme.panel),
                    ) {
                        VisualiserCanvas(
                            bands = spectrum.bands.value,
                            layers = settings.visualiserLayers,
                            theme = theme,
                            intensity = settings.visualiserIntensity,
                            palette = settings.visualiserColours.map { Color(it) },
                            modifier = Modifier.fillMaxSize(),
                        )
                        if (!spectrum.active.value) {
                            Text(
                                "Allow audio access in the Visualiser tab",
                                color = theme.textDim,
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.align(Alignment.Center).padding(16.dp),
                            )
                        }
                    }
                } else {
                    Cover(
                        model.coverUrl(song?.coverArt, size = 800),
                        Modifier.fillMaxSize(),
                        corner = 16,
                    )
                }
            }

            Text(
                if (showVisualiser) "Show artwork" else "Show visualiser",
                color = theme.accent,
                style = MaterialTheme.typography.labelLarge,
                modifier = Modifier.clickable { showVisualiser = !showVisualiser },
            )

            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    song?.title ?: "Nothing playing",
                    style = MaterialTheme.typography.titleLarge,
                    color = theme.text,
                    textAlign = TextAlign.Center,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    listOfNotNull(song?.artist, song?.album).joinToString(" — "),
                    style = MaterialTheme.typography.bodyMedium,
                    color = theme.textDim,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            Column(Modifier.fillMaxWidth()) {
                Slider(
                    value = scrubbing ?: playback.progress,
                    onValueChange = { scrubbing = it },
                    onValueChangeFinished = {
                        scrubbing?.let { model.player.seekToFraction(it) }
                        scrubbing = null
                    },
                )
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text((playback.positionMs / 1000).toInt().asClock(),
                        style = MaterialTheme.typography.labelSmall, color = theme.textDim)
                    Text((playback.durationMs / 1000).toInt().asClock(),
                        style = MaterialTheme.typography.labelSmall, color = theme.textDim)
                }
            }

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    Icons.Default.Shuffle, "Shuffle",
                    tint = if (playback.shuffle) theme.accent else theme.textDim,
                    modifier = Modifier.size(26.dp)
                        .clickable { model.player.setShuffle(!playback.shuffle) },
                )
                Icon(
                    Icons.Default.SkipPrevious, "Previous", tint = theme.text,
                    modifier = Modifier.size(40.dp).clickable { model.player.previous() },
                )
                Icon(
                    if (playback.playing) Icons.Default.Pause else Icons.Default.PlayArrow,
                    if (playback.playing) "Pause" else "Play",
                    tint = theme.accent,
                    modifier = Modifier.size(56.dp).clickable { model.player.playPause() },
                )
                Icon(
                    Icons.Default.SkipNext, "Next",
                    tint = if (playback.hasNext) theme.text else theme.textDim,
                    modifier = Modifier.size(40.dp).clickable { model.player.next() },
                )
                Icon(
                    if (playback.repeat == Player.REPEAT_MODE_ONE) Icons.Default.RepeatOne
                    else Icons.Default.Repeat,
                    "Repeat",
                    tint = if (playback.repeat == Player.REPEAT_MODE_OFF) theme.textDim
                    else theme.accent,
                    modifier = Modifier.size(26.dp).clickable { model.player.cycleRepeat() },
                )
            }

            song?.let { current ->
                Row(horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                    Icon(
                        if (current.starred) Icons.Default.Star else Icons.Default.StarBorder,
                        if (current.starred) "Unstar" else "Star",
                        tint = if (current.starred) theme.accent else theme.textDim,
                        modifier = Modifier.size(24.dp).clickable { model.toggleStar(current) },
                    )
                    Icon(
                        Icons.Default.Download,
                        if (current.downloaded) "Downloaded" else "Download",
                        tint = if (current.downloaded) theme.success else theme.textDim,
                        modifier = Modifier.size(24.dp).clickable {
                            if (current.downloaded) model.removeDownload(current)
                            else model.download(listOf(current))
                        },
                    )
                }
            }

            Spacer(Modifier.size(8.dp))
        }
    }
}
