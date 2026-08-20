package dev.rose.bouquet

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.media3.common.util.UnstableApi
import androidx.compose.runtime.getValue
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.ui.Shell
import dev.rose.bouquet.ui.theme.RoseBouquetTheme

@UnstableApi
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            val model: AppViewModel = viewModel()
            val settings by model.settings.collectAsStateWithLifecycle()

            RoseBouquetTheme(themeKey = settings.theme, styleKey = settings.style) {
                Shell(model)
            }
        }
    }
}
