package dev.rose.bouquet.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

/**
 * The Rose palette and style system, ported to Compose.
 *
 * Same split as the desktop apps: a **theme** is a flat set of named colours,
 * a **style** is the shape and weight of the interface, and any theme composes
 * with any style. The colour values are copied from `rose_bouquet/ui/theme.py`
 * verbatim so a theme looks the same on the phone as on the desktop — that is
 * the point of keeping them as plain data in both places.
 */

data class RoseTheme(
    val key: String,
    val label: String,
    val background: Color,
    val surface: Color,
    val panel: Color,
    val elevated: Color,
    val accent: Color,
    val accentMuted: Color,
    val text: Color,
    val textDim: Color,
    val border: Color,
    val error: Color,
    val isLight: Boolean = false,
)

data class RoseStyle(
    val key: String,
    val label: String,
    val radius: Int,
    val radiusLarge: Int,
    val radiusSmall: Int,
    val spacing: Int,
    val padding: Int,
)

private fun hex(value: String) = Color(android.graphics.Color.parseColor(value))

val ROSE_THEMES: List<RoseTheme> = listOf(
    RoseTheme("rose-dark", "Rose Dark", hex("#000000"), hex("#0d0d10"), hex("#14141a"),
        hex("#1c1c24"), hex("#e0607e"), hex("#8c3d4f"), hex("#e8e6ea"), hex("#8a8791"),
        hex("#22222c"), hex("#e06c75")),
    RoseTheme("rose-oled", "Rose OLED", hex("#000000"), hex("#000000"), hex("#0a0a0d"),
        hex("#141419"), hex("#e0607e"), hex("#7a3546"), hex("#f0eef2"), hex("#7c7986"),
        hex("#1a1a20"), hex("#e06c75")),
    RoseTheme("tokyo-night", "Tokyo Night", hex("#1a1b26"), hex("#16161e"), hex("#1e2030"),
        hex("#292e42"), hex("#7aa2f7"), hex("#3d5a8c"), hex("#c0caf5"), hex("#565a78"),
        hex("#292e42"), hex("#f7768e")),
    RoseTheme("catppuccin-mocha", "Catppuccin Mocha", hex("#1e1e2e"), hex("#181825"),
        hex("#313244"), hex("#45475a"), hex("#cba6f7"), hex("#6c5b8a"), hex("#cdd6f4"),
        hex("#6c7086"), hex("#45475a"), hex("#f38ba8")),
    RoseTheme("gruvbox", "Gruvbox Dark", hex("#1d2021"), hex("#282828"), hex("#32302f"),
        hex("#3c3836"), hex("#fabd2f"), hex("#8f7218"), hex("#ebdbb2"), hex("#928374"),
        hex("#3c3836"), hex("#fb4934")),
    RoseTheme("nord", "Nord", hex("#2e3440"), hex("#3b4252"), hex("#434c5e"), hex("#4c566a"),
        hex("#88c0d0"), hex("#4d707a"), hex("#eceff4"), hex("#7b88a1"), hex("#4c566a"),
        hex("#bf616a")),
    RoseTheme("rose-pine", "Rosé Pine", hex("#191724"), hex("#1f1d2e"), hex("#26233a"),
        hex("#312f44"), hex("#ebbcba"), hex("#8c6f6e"), hex("#e0def4"), hex("#6e6a86"),
        hex("#26233a"), hex("#eb6f92")),
    RoseTheme("everforest", "Everforest", hex("#2b3339"), hex("#323c41"), hex("#3a454a"),
        hex("#445055"), hex("#a7c080"), hex("#61754b"), hex("#d3c6aa"), hex("#859289"),
        hex("#445055"), hex("#e67e80")),
    RoseTheme("dracula", "Dracula", hex("#282a36"), hex("#21222c"), hex("#343746"),
        hex("#44475a"), hex("#bd93f9"), hex("#6b5296"), hex("#f8f8f2"), hex("#6272a4"),
        hex("#44475a"), hex("#ff5555")),
    RoseTheme("kanagawa", "Kanagawa", hex("#1f1f28"), hex("#16161d"), hex("#2a2a37"),
        hex("#363646"), hex("#7e9cd8"), hex("#4a5b80"), hex("#dcd7ba"), hex("#727169"),
        hex("#363646"), hex("#e82424")),
    RoseTheme("oxocarbon", "Oxocarbon", hex("#161616"), hex("#0f0f0f"), hex("#262626"),
        hex("#393939"), hex("#be95ff"), hex("#6b52a1"), hex("#f2f4f8"), hex("#7b7c7e"),
        hex("#393939"), hex("#ee5396")),
    RoseTheme("synthwave", "Synthwave", hex("#241b2f"), hex("#1a1425"), hex("#2d2140"),
        hex("#3b2d52"), hex("#ff7edb"), hex("#8c4478"), hex("#f4eee4"), hex("#8b7fa6"),
        hex("#3b2d52"), hex("#fe4450")),
    RoseTheme("matrix", "Matrix", hex("#000000"), hex("#020a02"), hex("#0a160a"),
        hex("#12240f"), hex("#00ff6a"), hex("#0a7a38"), hex("#c8f7d4"), hex("#4c8f63"),
        hex("#12240f"), hex("#ff5555")),
    RoseTheme("light", "Light", hex("#faf9fb"), hex("#ffffff"), hex("#f1eff4"),
        hex("#e6e3ea"), hex("#b34464"), hex("#d99cad"), hex("#1c1a20"), hex("#6e6a76"),
        hex("#dcd8e0"), hex("#b3384a"), isLight = true),
    RoseTheme("catppuccin-latte", "Catppuccin Latte", hex("#eff1f5"), hex("#ffffff"),
        hex("#e6e9ef"), hex("#dce0e8"), hex("#8839ef"), hex("#b8a1e0"), hex("#4c4f69"),
        hex("#8c8fa1"), hex("#dce0e8"), hex("#d20f39"), isLight = true),
    RoseTheme("rose-pine-dawn", "Rosé Pine Dawn", hex("#faf4ed"), hex("#fffaf3"),
        hex("#f2e9e1"), hex("#e8ded5"), hex("#b4637a"), hex("#dcb0bc"), hex("#575279"),
        hex("#9893a5"), hex("#e8ded5"), hex("#b4637a"), isLight = true),
    RoseTheme("high-contrast", "High Contrast", hex("#000000"), hex("#000000"), hex("#141414"),
        hex("#242424"), hex("#ffd400"), hex("#8a7300"), hex("#ffffff"), hex("#d0d0d0"),
        hex("#ffffff"), hex("#ff5c5c")),
)

val ROSE_STYLES: List<RoseStyle> = listOf(
    RoseStyle("rounded", "Rounded", 12, 18, 8, 18, 10),
    RoseStyle("sharp", "Sharp", 0, 0, 0, 18, 10),
    RoseStyle("soft", "Soft", 18, 26, 12, 20, 12),
    RoseStyle("pill", "Pill", 999, 28, 999, 18, 11),
    RoseStyle("compact", "Compact", 8, 12, 6, 10, 6),
    RoseStyle("spacious", "Spacious", 14, 22, 10, 28, 15),
)

val LocalRoseTheme = compositionLocalOf { ROSE_THEMES.first() }
val LocalRoseStyle = compositionLocalOf { ROSE_STYLES.first() }

fun themeFor(key: String, dark: Boolean): RoseTheme =
    ROSE_THEMES.firstOrNull { it.key == key }
        ?: ROSE_THEMES.first { it.isLight != dark }

fun styleFor(key: String): RoseStyle =
    ROSE_STYLES.firstOrNull { it.key == key } ?: ROSE_STYLES.first()

@Composable
fun RoseBouquetTheme(
    themeKey: String = "rose-dark",
    styleKey: String = "rounded",
    content: @Composable () -> Unit,
) {
    val theme = themeFor(themeKey, dark = !isSystemInDarkTheme().not())
    val style = styleFor(styleKey)

    val colours = if (theme.isLight) {
        lightColorScheme(
            primary = theme.accent,
            onPrimary = theme.background,
            secondary = theme.accentMuted,
            background = theme.background,
            onBackground = theme.text,
            surface = theme.surface,
            onSurface = theme.text,
            surfaceVariant = theme.panel,
            onSurfaceVariant = theme.textDim,
            outline = theme.border,
            error = theme.error,
        )
    } else {
        darkColorScheme(
            primary = theme.accent,
            onPrimary = theme.background,
            secondary = theme.accentMuted,
            background = theme.background,
            onBackground = theme.text,
            surface = theme.surface,
            onSurface = theme.text,
            surfaceVariant = theme.panel,
            onSurfaceVariant = theme.textDim,
            outline = theme.border,
            error = theme.error,
        )
    }

    // A pill style asks for a radius larger than any control is tall; Compose
    // clamps it the same way Qt does, which is what makes one absurd number do
    // the right thing at every size.
    val shapes = Shapes(
        small = androidx.compose.foundation.shape.RoundedCornerShape(style.radiusSmall.dp),
        medium = androidx.compose.foundation.shape.RoundedCornerShape(style.radius.dp),
        large = androidx.compose.foundation.shape.RoundedCornerShape(style.radiusLarge.dp),
    )

    androidx.compose.runtime.CompositionLocalProvider(
        LocalRoseTheme provides theme,
        LocalRoseStyle provides style,
    ) {
        MaterialTheme(colorScheme = colours, shapes = shapes, content = content)
    }
}
