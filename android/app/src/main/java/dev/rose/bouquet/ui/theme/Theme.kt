package dev.rose.bouquet.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp

/**
 * The Rose palette and style system, on the same split as the desktop apps: a
 * **theme** is a flat set of named colours, a **style** is the shape and weight
 * of the interface, and any theme composes with any style.
 *
 * Material's own `ColorScheme` is derived from a Rose theme rather than
 * replacing it, so stock Material components look right without every screen
 * having to restyle them — but screens that want a Rose colour by name reach
 * for [LocalRoseTheme], because Material has no slot that means "panel" or
 * "dim text" and mapping them onto ones that mean something else is how a
 * palette quietly stops matching itself.
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
    val success: Color,
    val warning: Color,
    val error: Color,
    val placeholder: Color,
    val isLight: Boolean,
)

data class RoseStyle(
    val key: String,
    val label: String,
    val radius: Int,
    val radiusLarge: Int,
    val radiusSmall: Int,
    val padding: Int,
    val fontSize: Int,
    val headingSize: Int,
    val borderWidth: Int,
    val lineHeight: Int,
    val elevatedPanels: Boolean,
)

val LocalRoseTheme = staticCompositionLocalOf { ROSE_THEMES.first() }
val LocalRoseStyle = staticCompositionLocalOf { ROSE_STYLES.first() }

/** The theme named [key], or a sensible one of the right lightness. */
fun themeFor(key: String, dark: Boolean = true): RoseTheme =
    ROSE_THEMES.firstOrNull { it.key == key }
        ?: ROSE_THEMES.first { it.isLight != dark }

fun styleFor(key: String): RoseStyle =
    ROSE_STYLES.firstOrNull { it.key == key } ?: ROSE_STYLES.first()

/**
 * The key meaning "whatever the system is set to".
 *
 * Kept as a reserved key rather than a separate boolean so the saved preference
 * stays a single string, and so following the system is one entry in the same
 * list the user picks a theme from.
 */
const val SYSTEM_THEME = "system"

@Composable
fun RoseBouquetTheme(
    themeKey: String = "rose-dark",
    styleKey: String = "rounded",
    content: @Composable () -> Unit,
) {
    val dark = isSystemInDarkTheme()
    val theme = if (themeKey == SYSTEM_THEME) {
        themeFor(if (dark) "rose-dark" else "light", dark)
    } else {
        themeFor(themeKey, dark)
    }
    val style = styleFor(styleKey)

    // Named rather than positional: `lightColorScheme` takes three dozen
    // same-typed arguments, so a positional call is one upstream reordering
    // away from swapping two colours and looking merely odd rather than broken.
    val scheme = with(theme) {
        if (isLight) {
            lightColorScheme(
                primary = accent, onPrimary = background,
                primaryContainer = accentMuted, onPrimaryContainer = text,
                inversePrimary = accentMuted,
                secondary = accentMuted, onSecondary = text,
                secondaryContainer = elevated, onSecondaryContainer = text,
                tertiary = accent, onTertiary = background,
                tertiaryContainer = elevated, onTertiaryContainer = text,
                background = background, onBackground = text,
                surface = surface, onSurface = text,
                surfaceVariant = panel, onSurfaceVariant = textDim,
                surfaceTint = accent, inverseSurface = text, inverseOnSurface = background,
                error = error, onError = background, errorContainer = error, onErrorContainer = text,
                outline = border, outlineVariant = border, scrim = Color.Black,
                surfaceBright = elevated, surfaceDim = background,
                surfaceContainer = panel, surfaceContainerHigh = elevated,
                surfaceContainerHighest = elevated, surfaceContainerLow = surface,
                surfaceContainerLowest = background,
            )
        } else {
            darkColorScheme(
                primary = accent, onPrimary = background,
                primaryContainer = accentMuted, onPrimaryContainer = text,
                inversePrimary = accentMuted,
                secondary = accentMuted, onSecondary = text,
                secondaryContainer = elevated, onSecondaryContainer = text,
                tertiary = accent, onTertiary = background,
                tertiaryContainer = elevated, onTertiaryContainer = text,
                background = background, onBackground = text,
                surface = surface, onSurface = text,
                surfaceVariant = panel, onSurfaceVariant = textDim,
                surfaceTint = accent, inverseSurface = text, inverseOnSurface = background,
                error = error, onError = background, errorContainer = error, onErrorContainer = text,
                outline = border, outlineVariant = border, scrim = Color.Black,
                surfaceBright = elevated, surfaceDim = background,
                surfaceContainer = panel, surfaceContainerHigh = elevated,
                surfaceContainerHighest = elevated, surfaceContainerLow = surface,
                surfaceContainerLowest = background,
            )
        }
    }

    // A pill style asks for a radius far larger than any control is tall.
    // Compose clamps it the way Qt does, which is what lets one absurd number
    // do the right thing at every size.
    val shapes = Shapes(
        extraSmall = RoundedCornerShape(style.radiusSmall.dp),
        small = RoundedCornerShape(style.radiusSmall.dp),
        medium = RoundedCornerShape(style.radius.dp),
        large = RoundedCornerShape(style.radiusLarge.dp),
        extraLarge = RoundedCornerShape(style.radiusLarge.dp),
    )

    // Styles state a base size and a leading percentage; everything else is
    // derived so a style only has to say one number to feel coherent.
    val base = style.fontSize.sp
    val leading = (style.lineHeight / 100f).em
    val typography = Typography(
        headlineLarge = TextStyle(fontSize = style.headingSize.sp, lineHeight = leading),
        headlineMedium = TextStyle(fontSize = (style.headingSize - 4).sp, lineHeight = leading),
        headlineSmall = TextStyle(fontSize = (style.headingSize - 8).sp, lineHeight = leading),
        titleLarge = TextStyle(fontSize = (style.fontSize + 4).sp, lineHeight = leading),
        titleMedium = TextStyle(fontSize = (style.fontSize + 2).sp, lineHeight = leading),
        titleSmall = TextStyle(fontSize = base, lineHeight = leading),
        bodyLarge = TextStyle(fontSize = base, lineHeight = leading),
        bodyMedium = TextStyle(fontSize = (style.fontSize - 1).sp, lineHeight = leading),
        bodySmall = TextStyle(fontSize = (style.fontSize - 2).sp, lineHeight = leading),
        labelLarge = TextStyle(fontSize = (style.fontSize - 1).sp, lineHeight = leading),
        labelMedium = TextStyle(fontSize = (style.fontSize - 2).sp, lineHeight = leading),
        labelSmall = TextStyle(fontSize = (style.fontSize - 3).sp, lineHeight = leading),
    )

    CompositionLocalProvider(
        LocalRoseTheme provides theme,
        LocalRoseStyle provides style,
    ) {
        MaterialTheme(colorScheme = scheme, shapes = shapes, typography = typography, content = content)
    }
}
