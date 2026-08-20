package dev.rose.bouquet.ui.screens

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.media3.common.util.UnstableApi
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.ui.LoadingLine
import dev.rose.bouquet.ui.SectionHeading
import dev.rose.bouquet.ui.theme.LocalRoseTheme

/**
 * Import: bring a history and playlists in from elsewhere.
 *
 * This is the tab that makes the recommender useful on the first day rather
 * than the hundredth, which is why it is not an afterthought.
 */
@UnstableApi
@Composable
fun ImportScreen(model: AppViewModel) {
    val theme = LocalRoseTheme.current

    // The import itself belongs to the view model, not to this composition: a
    // Takeout archive takes minutes to read, and a screen's own scope dies the
    // moment you look at another tab. Here the screen only shows what is going
    // on and can be left and come back to.
    val working by model.importing.collectAsState()
    val report by model.importReport.collectAsState()
    var spotifyUrl by remember { mutableStateOf("") }

    val pickTakeout = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri -> uri?.let(model::importTakeout) }

    val pickCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri -> uri?.let(model::importExportify) }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
        LoadingLine(working)

        report?.let {
            Text(
                it,
                color = theme.accent,
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(16.dp),
            )
        }

        SectionHeading("YouTube history")
        Text(
            "A recommender with no history has nothing to work from, so a new install " +
                "shows an empty Watch tab until you have watched a few things. Importing " +
                "your real history from Google skips that entirely.\n\n" +
                "Ask Google Takeout for YouTube history, download the .zip, and pick it " +
                "here — no need to unpack it first. Videos and shorts are kept apart, the " +
                "same way they are when watched here.",
            color = theme.textDim,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(horizontal = 16.dp),
        )
        Row(Modifier.padding(16.dp)) {
            Button(
                enabled = !working,
                onClick = { pickTakeout.launch(arrayOf("application/zip", "*/*")) },
            ) { Text("Choose a Takeout .zip") }
        }

        SectionHeading("Spotify playlists")
        Text(
            "Paste a public playlist link and each track is found on YouTube Music. " +
                "Spotify caps anonymous reads at 100 tracks — a longer playlist needs an " +
                "Exportify CSV instead, and a capped import says so rather than pretending " +
                "the playlist was short.",
            color = theme.textDim,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(horizontal = 16.dp),
        )
        OutlinedTextField(
            value = spotifyUrl,
            onValueChange = { spotifyUrl = it },
            placeholder = { Text("https://open.spotify.com/playlist/…") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        )
        Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) {
            Button(
                enabled = !working && spotifyUrl.isNotBlank(),
                onClick = { model.importSpotify(spotifyUrl) },
            ) { Text("Import playlist") }
            Spacer(Modifier.width(12.dp))
            OutlinedButton(
                enabled = !working,
                onClick = { pickCsv.launch(arrayOf("text/csv", "text/comma-separated-values", "*/*")) },
            ) { Text("Exportify CSV") }
        }

        Spacer(Modifier.size(32.dp))
    }
}
