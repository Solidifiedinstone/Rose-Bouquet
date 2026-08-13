package dev.rose.bouquet.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
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
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.util.UnstableApi
import androidx.navigation.NavController
import dev.rose.bouquet.data.SubsonicClient
import dev.rose.bouquet.data.db.SongEntity
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.ui.Cover
import dev.rose.bouquet.ui.CoverTile
import dev.rose.bouquet.ui.Empty
import dev.rose.bouquet.ui.LoadingLine
import dev.rose.bouquet.ui.SectionHeading
import dev.rose.bouquet.ui.SongRow
import dev.rose.bouquet.ui.theme.LocalRoseTheme

@UnstableApi
@Composable
fun LibraryScreen(model: AppViewModel, navController: NavController) {
    val songs by model.songs.collectAsStateWithLifecycle()
    val playback by model.playback.collectAsStateWithLifecycle()
    val refreshing by model.refreshing.collectAsStateWithLifecycle()
    val server by model.activeServer.collectAsStateWithLifecycle()
    val theme = LocalRoseTheme.current
    var confirmDownloadAll by remember { mutableStateOf(false) }

    if (confirmDownloadAll) {
        val pending = remember(songs) { songs.filter { !it.downloaded } }
        // Asked first, and told how much, because this is the one action here
        // that can fill a phone or a data allowance and cannot be undone by
        // pressing it again.
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { confirmDownloadAll = false },
            title = { Text("Download the whole library?") },
            text = {
                Text(
                    "${pending.size} tracks are not on this phone yet" +
                        (estimateSize(pending)?.let { ", roughly $it" } ?: "") +
                        ". Downloads run on wifi unless you have allowed mobile data " +
                        "in Settings."
                )
            },
            confirmButton = {
                androidx.compose.material3.TextButton(onClick = {
                    model.download(pending)
                    confirmDownloadAll = false
                }) { Text("Download all") }
            },
            dismissButton = {
                androidx.compose.material3.TextButton(
                    onClick = { confirmDownloadAll = false },
                ) { Text("Cancel") }
            },
        )
    }

    Column(Modifier.fillMaxSize()) {
        SectionHeading("Library") {
            Row(verticalAlignment = Alignment.CenterVertically) {
                // Remembered: this counts the whole library, and the header
                // recomposes whenever anything on the screen moves — with a
                // large library that was a full scan several times a second.
                val pending = remember(songs) { songs.count { !it.downloaded } }
                Icon(
                    if (pending == 0 && songs.isNotEmpty()) Icons.Default.DownloadDone
                    else Icons.Default.Download,
                    contentDescription =
                        if (pending == 0) "Everything is downloaded" else "Download all $pending",
                    tint = when {
                        songs.isEmpty() -> theme.textDim
                        pending == 0 -> theme.success
                        else -> theme.accent
                    },
                    modifier = Modifier.size(24.dp)
                        .clickable(enabled = pending > 0) { confirmDownloadAll = true },
                )
                Spacer(Modifier.width(18.dp))
                Icon(
                    Icons.Default.Refresh,
                    contentDescription = "Rescan",
                    tint = if (refreshing) theme.textDim else theme.accent,
                    modifier = Modifier.size(24.dp)
                        .clickable(enabled = !refreshing) { model.refresh() },
                )
            }
        }
        LoadingLine(refreshing)

        when {
            server == null -> Empty(
                "No server yet",
                "Add the address of a Rose Bouquet, Navidrome, Airsonic or Gonic server in " +
                    "Settings, and your library appears here.",
            )
            songs.isEmpty() && refreshing -> Empty("Scanning…", "Reading the library off the server.")
            songs.isEmpty() -> Empty(
                "Nothing here yet",
                "Pull the library from ${server?.displayName} with the refresh button above.",
            )
            else -> LazyColumn(Modifier.fillMaxSize()) {
                itemsIndexed(songs) { index, song ->
                    SongRow(
                        song = song,
                        coverUrl = model.coverUrl(song.coverArt, size = 128),
                        playing = playback.song?.id == song.id,
                        onClick = { model.play(songs, index) },
                        onDownload = { model.toggleDownload(song) },
                    ) {
                        Icon(
                            if (song.starred) Icons.Default.Star else Icons.Default.StarBorder,
                            contentDescription = if (song.starred) "Unstar" else "Star",
                            tint = if (song.starred) theme.accent else theme.textDim,
                            modifier = Modifier.size(20.dp).clickable { model.toggleStar(song) },
                        )
                    }
                }
            }
        }
    }
}

@UnstableApi
@Composable
fun AlbumsScreen(model: AppViewModel) {
    val albums by model.albums.collectAsStateWithLifecycle()
    var openId by remember { mutableStateOf<String?>(null) }

    val open = albums.firstOrNull { it.id == openId }
    if (open != null) {
        AlbumDetail(model, open.id, open.name, open.artist, open.coverArt) { openId = null }
        return
    }

    Column(Modifier.fillMaxSize()) {
        SectionHeading("Albums")
        if (albums.isEmpty()) {
            Empty("No albums", "Once the library has been scanned, albums appear here.")
        } else {
            LazyVerticalGrid(
                columns = GridCells.Adaptive(minSize = 150.dp),
                modifier = Modifier.fillMaxSize(),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(8.dp),
            ) {
                items(albums, key = { it.id }) { album ->
                    CoverTile(
                        title = album.name,
                        subtitle = album.artist,
                        coverUrl = model.coverUrl(album.coverArt, size = 300),
                        onClick = { openId = album.id },
                    )
                }
            }
        }
    }
}

@UnstableApi
@Composable
private fun AlbumDetail(
    model: AppViewModel,
    albumId: String,
    name: String,
    artist: String,
    coverArt: String?,
    onBack: () -> Unit,
) {
    val allSongs by model.songs.collectAsStateWithLifecycle()
    val playback by model.playback.collectAsStateWithLifecycle()
    val theme = LocalRoseTheme.current
    val songs = remember(allSongs, albumId) { allSongs.filter { it.albumId == albumId } }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("‹", style = MaterialTheme.typography.headlineMedium, color = theme.accent,
                modifier = Modifier.clickable(onClick = onBack).padding(end = 12.dp))
            Cover(model.coverUrl(coverArt, size = 300), Modifier.size(72.dp))
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(name, style = MaterialTheme.typography.titleMedium, color = theme.text)
                Text(artist, style = MaterialTheme.typography.bodySmall, color = theme.textDim)
                Text("${songs.size} tracks", style = MaterialTheme.typography.bodySmall,
                    color = theme.textDim)
            }
            Icon(
                Icons.Default.Download,
                contentDescription = "Download this album",
                tint = theme.accent,
                modifier = Modifier.size(24.dp).clickable { model.download(songs) },
            )
        }

        LazyColumn(Modifier.fillMaxSize()) {
            itemsIndexed(songs, key = { _, song -> song.id }) { index, song ->
                SongRow(
                    song = song,
                    coverUrl = model.coverUrl(song.coverArt, size = 128),
                    playing = playback.song?.id == song.id,
                    onClick = { model.play(songs, index) },
                    onDownload = { model.toggleDownload(song) },
                )
            }
        }
    }
}

@UnstableApi
@Composable
fun SearchScreen(model: AppViewModel) {
    var query by remember { mutableStateOf("") }
    var results by remember { mutableStateOf<List<SongEntity>>(emptyList()) }
    var searching by remember { mutableStateOf(false) }
    val playback by model.playback.collectAsStateWithLifecycle()
    val theme = LocalRoseTheme.current

    // Debounced: a request per keystroke is a request per keystroke, and on a
    // phone that is somebody's data allowance as well as the server's time.
    LaunchedEffect(query) {
        if (query.isBlank()) {
            results = emptyList()
            return@LaunchedEffect
        }
        kotlinx.coroutines.delay(300)
        searching = true
        results = model.search(query)
        searching = false
    }

    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = query,
            onValueChange = { query = it },
            placeholder = { Text("Search your library") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(16.dp),
        )
        LoadingLine(searching)

        when {
            query.isBlank() -> Empty("Search", "Find anything in the library on your server.")
            results.isEmpty() && !searching -> Empty("Nothing found", "No track, album or artist matches “$query”.")
            else -> LazyColumn(Modifier.fillMaxSize()) {
                itemsIndexed(results, key = { _, song -> song.id }) { index, song ->
                    SongRow(
                        song = song,
                        coverUrl = model.coverUrl(song.coverArt, size = 128),
                        playing = playback.song?.id == song.id,
                        onClick = { model.play(results, index) },
                        onDownload = { model.toggleDownload(song) },
                    )
                }
            }
        }
    }
}

@UnstableApi
@Composable
fun DownloadsScreen(model: AppViewModel) {
    val downloads by model.downloads.collectAsStateWithLifecycle()
    val playback by model.playback.collectAsStateWithLifecycle()
    val theme = LocalRoseTheme.current

    Column(Modifier.fillMaxSize()) {
        SectionHeading("Downloads")
        if (downloads.isEmpty()) {
            Empty(
                "Nothing downloaded",
                "Downloaded music plays with no server and no signal. Use the download " +
                    "button on any album to keep it on this phone.",
            )
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                itemsIndexed(downloads, key = { _, song -> song.id }) { index, song ->
                    SongRow(
                        song = song,
                        coverUrl = model.coverUrl(song.coverArt, size = 128),
                        playing = playback.song?.id == song.id,
                        onClick = { model.play(downloads, index) },
                    ) {
                        Text(
                            "Remove",
                            style = MaterialTheme.typography.labelSmall,
                            color = theme.textDim,
                            modifier = Modifier.clickable { model.removeDownload(song) },
                        )
                    }
                }
            }
        }
    }
}

@UnstableApi
@Composable
fun PlaylistsScreen(model: AppViewModel) {
    var playlists by remember { mutableStateOf<List<SubsonicClient.Playlist>>(emptyList()) }
    var openId by remember { mutableStateOf<String?>(null) }
    var songs by remember { mutableStateOf<List<SongEntity>>(emptyList()) }
    val playback by model.playback.collectAsStateWithLifecycle()
    val theme = LocalRoseTheme.current

    LaunchedEffect(Unit) { playlists = model.playlists() }
    LaunchedEffect(openId) {
        songs = openId?.let { model.playlistSongs(it) }.orEmpty()
    }

    if (openId != null) {
        Column(Modifier.fillMaxSize()) {
            SectionHeading(playlists.firstOrNull { it.id == openId }?.name ?: "Playlist") {
                Text("Back", color = theme.accent,
                    modifier = Modifier.clickable { openId = null })
            }
            LazyColumn(Modifier.fillMaxSize()) {
                items(songs.size) { index ->
                    val song = songs[index]
                    SongRow(
                        song = song,
                        coverUrl = model.coverUrl(song.coverArt, size = 128),
                        playing = playback.song?.id == song.id,
                        onClick = { model.play(songs, index) },
                        onDownload = { model.toggleDownload(song) },
                    )
                }
            }
        }
        return
    }

    Column(Modifier.fillMaxSize()) {
        SectionHeading("Playlists")
        if (playlists.isEmpty()) {
            Empty(
                "No playlists",
                "Playlists on your server appear here. Not every server offers them — " +
                    "the Rose Bouquet desktop server keeps playlists as plain M3U files.",
            )
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(playlists, key = { it.id }) { playlist ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clickable { openId = playlist.id }
                            .padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text(playlist.name, color = theme.text,
                                style = MaterialTheme.typography.bodyLarge)
                            Text("${playlist.songCount} tracks", color = theme.textDim,
                                style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }
}


/**
 * Roughly how much space a set of tracks needs.
 *
 * Null when the server did not report sizes — a guessed number people plan
 * around is worse than none, so it simply is not shown.
 */
private fun estimateSize(songs: List<dev.rose.bouquet.data.db.SongEntity>): String? {
    val known = songs.mapNotNull { it.sizeBytes }.filter { it > 0 }
    if (known.size < songs.size / 2) return null

    // Scaled up for the tracks with no reported size, so the figure is not
    // quietly an underestimate of the thing about to fill somebody's phone.
    val total = known.sum() * songs.size / known.size.coerceAtLeast(1)
    val mb = total / 1_048_576.0
    return if (mb >= 1024) "%.1f GB".format(mb / 1024) else "%.0f MB".format(mb)
}
