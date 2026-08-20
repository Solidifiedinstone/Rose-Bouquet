package dev.rose.bouquet.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.util.UnstableApi
import androidx.compose.ui.platform.LocalContext
import dev.rose.bouquet.BuildConfig
import dev.rose.bouquet.data.Updates
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.ui.SectionHeading
import dev.rose.bouquet.ui.theme.LocalRoseTheme
import dev.rose.bouquet.ui.theme.ROSE_STYLES
import dev.rose.bouquet.ui.theme.ROSE_THEMES
import kotlinx.coroutines.launch

@UnstableApi
@Composable
fun SettingsScreen(model: AppViewModel) {
    val theme = LocalRoseTheme.current
    val settings by model.settings.collectAsStateWithLifecycle()
    val servers by model.serverList.collectAsStateWithLifecycle()
    val active by model.activeServer.collectAsStateWithLifecycle()

    var addingServer by remember { mutableStateOf(false) }

    LazyColumn(Modifier.fillMaxSize()) {
        item { SectionHeading("Servers") }

        items(servers, key = { it.id }) { server ->
            Row(
                Modifier
                    .fillMaxWidth()
                    .clickable { model.setActiveServer(server.id) }
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        server.displayName,
                        color = if (server.id == active?.id) theme.accent else theme.text,
                        style = MaterialTheme.typography.bodyLarge,
                    )
                    Text(server.url, color = theme.textDim,
                        style = MaterialTheme.typography.bodySmall)
                }
                if (server.id == active?.id) {
                    Text("In use", color = theme.accent,
                        style = MaterialTheme.typography.labelSmall)
                    Spacer(Modifier.width(12.dp))
                }
                Text("Remove", color = theme.error, style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.clickable { model.removeServer(server.id) })
            }
        }

        item {
            Row(Modifier.padding(16.dp)) {
                Button(onClick = { addingServer = true }) { Text("Add a server") }
                Spacer(Modifier.width(12.dp))
                TextButton(onClick = { model.refresh() }) { Text("Rescan library") }
            }
        }

        // ── Appearance ────────────────────────────────────────────

        item { SectionHeading("Theme") }
        item {
            LazyRow(
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(ROSE_THEMES, key = { it.key }) { entry ->
                    FilterChip(
                        selected = settings.theme == entry.key,
                        onClick = { model.setTheme(entry.key) },
                        label = { Text(entry.label) },
                    )
                }
            }
        }

        item { SectionHeading("Style") }
        item {
            LazyRow(
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(ROSE_STYLES, key = { it.key }) { entry ->
                    FilterChip(
                        selected = settings.style == entry.key,
                        onClick = { model.setStyle(entry.key) },
                        label = { Text(entry.label) },
                    )
                }
            }
        }

        // ── The algorithm ─────────────────────────────────────────

        item { SectionHeading("What you see") }
        item {
            Toggle(
                "Filter engagement bait and AI slop",
                "Hides videos whose titles read as generated filler or bait. On by default.",
                settings.filterSlop, model::setFilterSlop,
            )
        }
        item {
            WordList(
                title = "Interests",
                detail = "Topics you want more of. These outweigh everything else the app infers.",
                words = settings.interests,
                onChange = model::setInterests,
            )
        }
        item {
            WordList(
                title = "Never show me",
                detail = "Words in a title that mean you never want to see it. This removes, " +
                    "rather than merely demoting.",
                words = settings.blocked,
                onChange = model::setBlocked,
            )
        }
        item {
            WordList(
                title = "Blocked channels",
                detail = "Channels you never want to see, by name.",
                words = settings.blockedChannels,
                onChange = model::setBlockedChannels,
            )
        }

        // ── Playback ──────────────────────────────────────────────

        item { SectionHeading("Playback") }
        item {
            Toggle(
                "Tell the server what you played",
                "Scrobbles to your server so play counts and recently-played work.",
                settings.scrobble, model::setScrobble,
            )
        }
        item {
            Toggle(
                "Download over mobile data",
                "Off by default — a queued discography on mobile data is not a mistake you " +
                    "can undo.",
                settings.downloadOnMobile, model::setDownloadOnMobile,
            )
        }
        item {
            Toggle(
                "Stream on wifi only",
                "On mobile data, only downloaded music plays. Nothing is streamed.",
                settings.wifiOnlyStreaming, model::setWifiOnlyStreaming,
            )
        }
        item {
            BitrateChoice(settings.maxBitrate, model::setMaxBitrate)
        }
        item {
            Toggle(
                "Music only",
                "Hides the Watch and Shorts tabs entirely.",
                settings.musicOnly, model::setMusicOnly,
            )
        }
        item {
            Toggle(
                "Show Playlists",
                "Your playlists are kept either way — this only hides the tab.",
                settings.showPlaylists, model::setShowPlaylists,
            )
        }

        item {
            UpdateRow()
        }

        item {
            Column(Modifier.padding(16.dp)) {
                Text("Bouquet", color = theme.text,
                    style = MaterialTheme.typography.titleSmall)
                Text(
                    "Version ${BuildConfig.VERSION_NAME} (build ${BuildConfig.VERSION_CODE})",
                    color = theme.textDim, style = MaterialTheme.typography.bodySmall,
                )
                Text("Rose Bouquet for Android", color = theme.textDim,
                    style = MaterialTheme.typography.bodySmall)
                Text("A project of R.O.S.E. — Rose Open Source Endeavours",
                    color = theme.textDim, style = MaterialTheme.typography.bodySmall)
            }
        }
    }

    if (addingServer) {
        AddServerDialog(model) { addingServer = false }
    }
}

/**
 * The bitrate ceiling asked of the server on mobile data.
 *
 * Only on mobile data, which is why the caption says so: a ceiling that applied
 * on wifi too would quietly hand you a worse copy of music you already own.
 * A server that cannot transcode ignores it, and then there was never a cheaper
 * version to be had.
 */
@Composable
private fun BitrateChoice(current: Int, onChange: (Int) -> Unit) {
    val theme = LocalRoseTheme.current
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        Text("Quality on mobile data", color = theme.text,
            style = MaterialTheme.typography.bodyLarge)
        Text(
            "Asks the server to transcode down. Only applies on mobile data — " +
                "wifi always gets the original.",
            color = theme.textDim, style = MaterialTheme.typography.bodySmall,
        )
        Spacer(Modifier.size(8.dp))
        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(BITRATES) { (kbps, label) ->
                FilterChip(
                    selected = current == kbps,
                    onClick = { onChange(kbps) },
                    label = { Text(label) },
                )
            }
        }
    }
}

private val BITRATES = listOf(
    0 to "Original",
    320 to "320k",
    192 to "192k",
    128 to "128k",
    96 to "96k",
)

/**
 * Check for a new version, and install it.
 *
 * Nothing is installed silently — the APK is downloaded and handed to Android's
 * own installer, which asks. An app able to replace itself without being asked
 * would be a worse thing to carry than the three manual steps this saves.
 */
@Composable
private fun UpdateRow() {
    val theme = LocalRoseTheme.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var checking by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var found by remember { mutableStateOf<Updates.Release?>(null) }

    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        SectionHeading("Updates")
        Text(
            "Installed: ${BuildConfig.VERSION_NAME} (build ${BuildConfig.VERSION_CODE})",
            color = theme.textDim, style = MaterialTheme.typography.bodySmall,
        )
        message?.let {
            Spacer(Modifier.size(6.dp))
            Text(it, color = theme.textDim, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.size(10.dp))

        Row(verticalAlignment = Alignment.CenterVertically) {
            Button(
                enabled = !checking,
                onClick = {
                    scope.launch {
                        checking = true
                        message = "Looking…"
                        val latest = Updates.latest()
                        message = when {
                            latest == null ->
                                "Could not check. No connection, or the release page is private " +
                                    "to an account this phone is not signed in to."
                            Updates.isNewer(latest.version, BuildConfig.VERSION_NAME) -> {
                                found = latest
                                "Version ${latest.version} is available."
                            }
                            else -> "This is the newest version."
                        }
                        checking = false
                    }
                },
            ) { Text(if (checking) "Checking…" else "Check for updates") }

            found?.let { release ->
                Spacer(Modifier.width(12.dp))
                Button(
                    enabled = !checking && release.apkUrl != null,
                    onClick = {
                        scope.launch {
                            checking = true
                            message = "Downloading ${release.version}…"
                            val intent = Updates.download(context, release.apkUrl!!)
                            if (intent == null) {
                                message = "The download failed."
                            } else {
                                message = "Android will ask you to confirm the install."
                                context.startActivity(intent)
                            }
                            checking = false
                        }
                    },
                ) { Text("Install ${release.version}") }
            }
        }
    }
}

@Composable
private fun Toggle(title: String, detail: String, value: Boolean, onChange: (Boolean) -> Unit) {
    val theme = LocalRoseTheme.current
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, color = theme.text, style = MaterialTheme.typography.bodyLarge)
            Text(detail, color = theme.textDim, style = MaterialTheme.typography.bodySmall)
        }
        Spacer(Modifier.width(12.dp))
        Switch(checked = value, onCheckedChange = onChange)
    }
}

/**
 * A set of words the user maintains.
 *
 * Chips with an obvious way to remove each one, because the whole promise of
 * this screen is that what it lists is what is enforced — a list you cannot
 * easily correct is how "configure your algorithm" becomes a lie.
 */
@Composable
private fun WordList(
    title: String,
    detail: String,
    words: Set<String>,
    onChange: (Set<String>) -> Unit,
) {
    val theme = LocalRoseTheme.current
    var entry by remember { mutableStateOf("") }

    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        Text(title, color = theme.text, style = MaterialTheme.typography.bodyLarge)
        Text(detail, color = theme.textDim, style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.size(8.dp))

        if (words.isNotEmpty()) {
            LazyRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                items(words.toList()) { word ->
                    AssistChip(
                        onClick = { onChange(words - word) },
                        label = { Text("$word  ✕") },
                    )
                }
            }
            Spacer(Modifier.size(8.dp))
        }

        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(
                value = entry,
                onValueChange = { entry = it },
                placeholder = { Text("Add a word") },
                singleLine = true,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            TextButton(
                onClick = {
                    val word = entry.trim().lowercase()
                    if (word.isNotBlank()) onChange(words + word)
                    entry = ""
                },
                enabled = entry.isNotBlank(),
            ) { Text("Add") }
        }
    }
}

@UnstableApi
@Composable
private fun AddServerDialog(model: AppViewModel, onDismiss: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("http://") }
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var testing by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add a server") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(name, { name = it }, label = { Text("Name (optional)") },
                    singleLine = true)
                OutlinedTextField(url, { url = it }, label = { Text("Address") },
                    placeholder = { Text("http://192.168.1.10:4533") }, singleLine = true)
                OutlinedTextField(username, { username = it }, label = { Text("Username") },
                    singleLine = true)
                OutlinedTextField(password, { password = it }, label = { Text("Password") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation())
                error?.let {
                    Text(it, color = LocalRoseTheme.current.error,
                        style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = !testing && url.isNotBlank() && username.isNotBlank(),
                onClick = {
                    scope.launch {
                        testing = true
                        error = null
                        // Checked before saving: a wrong password that is only
                        // discovered later looks like an empty library.
                        model.testServer(url, username, password)
                            .onSuccess {
                                model.addServer(name, url, username, password)
                                onDismiss()
                            }
                            .onFailure { error = it.message ?: "Could not reach that server" }
                        testing = false
                    }
                },
            ) { Text(if (testing) "Checking…" else "Add") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

@UnstableApi
@Composable
fun FollowingScreen(model: AppViewModel) {
    val channels by model.channels.collectAsStateWithLifecycle()
    val theme = LocalRoseTheme.current

    Column(Modifier.fillMaxSize()) {
        SectionHeading("Following")
        if (channels.isEmpty()) {
            dev.rose.bouquet.ui.Empty(
                "Not following anything",
                "Follow a channel from any video and it appears here. Subscriptions live on " +
                    "this phone, not on a YouTube account.",
            )
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(channels, key = { it.id }) { channel ->
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(channel.name, color = if (channel.muted) theme.textDim else theme.text,
                            style = MaterialTheme.typography.bodyLarge,
                            modifier = Modifier.weight(1f))
                        Text(
                            if (channel.muted) "Unmute" else "Mute",
                            color = theme.textDim,
                            style = MaterialTheme.typography.labelSmall,
                            modifier = Modifier.clickable {
                                model.setMuted(channel.id, !channel.muted)
                            },
                        )
                        Spacer(Modifier.width(16.dp))
                        Text("Unfollow", color = theme.error,
                            style = MaterialTheme.typography.labelSmall,
                            modifier = Modifier.clickable { model.unfollow(channel.id) })
                    }
                }
            }
        }
    }
}
