package dev.rose.bouquet.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Album
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Explore
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.ImportExport
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.filled.LibraryMusic
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.PlaylistPlay
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SkipNext
import androidx.compose.material.icons.filled.Subscriptions
import androidx.compose.material.icons.filled.Videocam
import androidx.compose.material.icons.filled.ViewDay
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.NavigationDrawerItem
import androidx.compose.material3.NavigationDrawerItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Snackbar
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.util.UnstableApi
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import dev.rose.bouquet.ui.screens.AlbumsScreen
import dev.rose.bouquet.ui.screens.BrowseScreen
import dev.rose.bouquet.ui.screens.DownloadsScreen
import dev.rose.bouquet.ui.screens.FollowingScreen
import dev.rose.bouquet.ui.screens.ImportScreen
import dev.rose.bouquet.ui.screens.LibraryScreen
import dev.rose.bouquet.ui.screens.NowPlayingSheet
import dev.rose.bouquet.ui.screens.PlaylistsScreen
import dev.rose.bouquet.ui.screens.SearchScreen
import dev.rose.bouquet.ui.screens.SettingsScreen
import dev.rose.bouquet.ui.screens.ShortsScreen
import dev.rose.bouquet.ui.screens.VisualiserScreen
import dev.rose.bouquet.ui.screens.WatchScreen
import dev.rose.bouquet.ui.screens.YouTubeMusicScreen
import dev.rose.bouquet.ui.theme.LocalRoseTheme
import kotlinx.coroutines.launch

/**
 * A destination in the bottom bar or the overflow.
 *
 * The desktop app has twelve sections down a sidebar. A phone has room for
 * five before the labels stop being readable, so the rest live one level in
 * — chosen by what somebody opens the app *to do*, not by what is
 * architecturally parallel.
 */
enum class Section(
    val route: String,
    val label: String,
    val icon: ImageVector,
    val primary: Boolean,
    val video: Boolean = false,
) {
    Watch("watch", "Watch", Icons.Default.Videocam, primary = true, video = true),
    Shorts("shorts", "Shorts", Icons.Default.ViewDay, primary = true, video = true),
    Library("library", "Library", Icons.Default.LibraryMusic, primary = true),
    Albums("albums", "Albums", Icons.Default.Album, primary = true),
    Search("search", "Search", Icons.Default.Search, primary = true),

    // Reached from the drawer. Everything here is as reachable as a bar tab —
    // one tap on the menu — and none of it is optional to the app working.
    Browse("browse", "Browse", Icons.Default.Explore, primary = false),
    YouTubeMusic("ytmusic", "YouTube Music", Icons.Default.MusicNote, primary = false, video = true),
    Playlists("playlists", "Playlists", Icons.Default.PlaylistPlay, primary = false),
    Following("following", "Following", Icons.Default.Subscriptions, primary = false, video = true),
    Downloads("downloads", "Downloads", Icons.Default.Download, primary = false),
    Import("import", "Import", Icons.Default.ImportExport, primary = false),
    Visualiser("visualiser", "Visualiser", Icons.Default.GraphicEq, primary = false),
    Settings("settings", "Settings", Icons.Default.Settings, primary = false),
}

@OptIn(ExperimentalMaterial3Api::class)
@UnstableApi
@Composable
fun Shell(model: AppViewModel) {
    val navController = rememberNavController()
    val backStack by navController.currentBackStackEntryAsState()
    val current = backStack?.destination

    val settings by model.settings.collectAsStateWithLifecycle()
    val playback by model.playback.collectAsStateWithLifecycle()
    val status by model.status.collectAsStateWithLifecycle()

    var sheetOpen by remember { mutableStateOf(false) }

    // With the video half switched off, the tabs that lead there should not be
    // sitting in the bar greyed out — they should not be there at all.
    val bar = Section.entries.filter { it.primary && (!settings.musicOnly || !it.video) }
    val drawerSections = Section.entries.filter { !it.primary && (!settings.musicOnly || !it.video) }
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val scope = rememberCoroutineScope()

    fun go(section: Section) {
        navController.navigate(section.route) {
            popUpTo(navController.graph.findStartDestination().id) { saveState = true }
            launchSingleTop = true
            restoreState = true
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet(drawerContainerColor = LocalRoseTheme.current.surface) {
                Text(
                    "Bouquet",
                    style = MaterialTheme.typography.headlineSmall,
                    color = LocalRoseTheme.current.accent,
                    modifier = Modifier.padding(24.dp),
                )
                drawerSections.forEach { section ->
                    NavigationDrawerItem(
                        selected = current?.hierarchy?.any { it.route == section.route } == true,
                        onClick = { scope.launch { drawerState.close() }; go(section) },
                        icon = { Icon(section.icon, contentDescription = null) },
                        label = { Text(section.label) },
                        modifier = Modifier.padding(NavigationDrawerItemDefaults.ItemPadding),
                    )
                }
            }
        },
    ) {
    Scaffold(
        containerColor = LocalRoseTheme.current.background,
        topBar = {
            TopAppBar(
                title = { Text(current?.sectionLabel() ?: "Bouquet") },
                navigationIcon = {
                    Icon(
                        Icons.Default.Menu,
                        contentDescription = "Menu",
                        tint = LocalRoseTheme.current.text,
                        modifier = Modifier
                            .padding(horizontal = 12.dp)
                            .size(24.dp)
                            .clickable { scope.launch { drawerState.open() } },
                    )
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = LocalRoseTheme.current.surface,
                    titleContentColor = LocalRoseTheme.current.text,
                ),
            )
        },
        bottomBar = {
            Column {
                MiniPlayer(
                    playback = playback,
                    coverUrl = model.coverUrl(playback.song?.coverArt, size = 128),
                    onPlayPause = model.player::playPause,
                    onNext = { model.player.next() },
                    onOpen = { sheetOpen = true },
                )
                NavigationBar(containerColor = LocalRoseTheme.current.surface) {
                    bar.forEach { section ->
                        val selected = current?.hierarchy?.any { it.route == section.route } == true
                        NavigationBarItem(
                            selected = selected,
                            // Tapping a tab returns to it rather than
                            // stacking another copy on top.
                            onClick = { go(section) },
                            icon = { Icon(section.icon, contentDescription = section.label) },
                            label = { Text(section.label) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = LocalRoseTheme.current.accent,
                                selectedTextColor = LocalRoseTheme.current.accent,
                                indicatorColor = LocalRoseTheme.current.elevated,
                                unselectedIconColor = LocalRoseTheme.current.textDim,
                                unselectedTextColor = LocalRoseTheme.current.textDim,
                            ),
                        )
                    }
                }
            }
        },
        snackbarHost = {
            status?.let {
                Snackbar(
                    containerColor = LocalRoseTheme.current.elevated,
                    contentColor = LocalRoseTheme.current.text,
                    modifier = Modifier.padding(12.dp),
                ) { Text(it) }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = if (settings.musicOnly) Section.Library.route else Section.Watch.route,
            modifier = Modifier.padding(padding),
        ) {
            composable(Section.Watch.route) { WatchScreen(model, navController) }
            composable(Section.Shorts.route) { ShortsScreen(model) }
            composable(Section.Library.route) { LibraryScreen(model, navController) }
            composable(Section.Albums.route) { AlbumsScreen(model) }
            composable(Section.Search.route) { SearchScreen(model) }
            composable(Section.Playlists.route) { PlaylistsScreen(model) }
            composable(Section.Downloads.route) { DownloadsScreen(model) }
            composable(Section.Following.route) { FollowingScreen(model) }
            composable(Section.Browse.route) { BrowseScreen(model) }
            composable(Section.YouTubeMusic.route) { YouTubeMusicScreen(model) }
            composable(Section.Import.route) { ImportScreen(model) }
            composable(Section.Visualiser.route) { VisualiserScreen(model) }
            composable(Section.Settings.route) { SettingsScreen(model) }
        }
    }
    }

    if (sheetOpen) {
        NowPlayingSheet(model = model, onDismiss = { sheetOpen = false })
    }

    // A status message is news, not a permanent condition.
    LaunchedEffect(status) {
        if (status != null) {
            kotlinx.coroutines.delay(4_000)
            model.clearStatus()
        }
    }
}

/**
 * The bar above the tabs showing what is playing.
 *
 * Present only when there is something to show — a permanent empty strip eats
 * the smallest screens for nothing. The progress line is the whole of the
 * position display here; a number would not be readable at this height and the
 * full transport is one tap away.
 */
@Composable
private fun MiniPlayer(
    playback: dev.rose.bouquet.player.PlaybackState,
    coverUrl: String?,
    onPlayPause: () -> Unit,
    onNext: () -> Unit,
    onOpen: () -> Unit,
) {
    val theme = LocalRoseTheme.current
    val song = playback.song

    AnimatedVisibility(
        visible = song != null,
        enter = slideInVertically { it },
        exit = slideOutVertically { it },
    ) {
        Column(Modifier.fillMaxWidth().background(theme.panel)) {
            LinearProgressIndicator(
                progress = { playback.progress },
                modifier = Modifier.fillMaxWidth().height(2.dp),
                color = theme.accent,
                trackColor = theme.border,
                drawStopIndicator = {},
            )
            Row(
                Modifier
                    .fillMaxWidth()
                    .clickable(onClick = onOpen)
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Cover(coverUrl, Modifier.size(40.dp), corner = 6)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Text(
                        song?.title.orEmpty(),
                        style = MaterialTheme.typography.bodyMedium,
                        color = theme.text,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        song?.artist.orEmpty(),
                        style = MaterialTheme.typography.bodySmall,
                        color = theme.textDim,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Icon(
                    if (playback.playing) Icons.Default.Pause else Icons.Default.PlayArrow,
                    contentDescription = if (playback.playing) "Pause" else "Play",
                    tint = theme.text,
                    modifier = Modifier
                        .size(32.dp)
                        .clickable(onClick = onPlayPause)
                        .padding(4.dp),
                )
                Icon(
                    Icons.Default.SkipNext,
                    contentDescription = "Next",
                    tint = if (playback.hasNext) theme.text else theme.textDim,
                    modifier = Modifier
                        .size(32.dp)
                        .clickable(enabled = playback.hasNext, onClick = onNext)
                        .padding(4.dp),
                )
            }
        }
    }
}

/** A row of content with a heading, used by most screens. */
@Composable
fun SectionHeading(text: String, modifier: Modifier = Modifier, action: (@Composable () -> Unit)? = null) {
    val theme = LocalRoseTheme.current
    Row(
        modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(text, style = MaterialTheme.typography.titleMedium, color = theme.text)
        action?.invoke()
    }
}

@Composable
fun LoadingLine(visible: Boolean) {
    if (!visible) return
    val theme = LocalRoseTheme.current
    LinearProgressIndicator(
        modifier = Modifier.fillMaxWidth().height(2.dp),
        color = theme.accent,
        trackColor = theme.border,
    )
}

@Composable
fun FullBleedBox(content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { content() }
}

/** The heading for whichever section is on screen. */
private fun androidx.navigation.NavDestination.sectionLabel(): String? =
    Section.entries.firstOrNull { it.route == route }?.label
