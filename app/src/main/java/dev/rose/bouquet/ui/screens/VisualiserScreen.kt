package dev.rose.bouquet.ui.screens

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.media3.common.util.UnstableApi
import dev.rose.bouquet.player.Spectrum
import dev.rose.bouquet.ui.AppViewModel
import dev.rose.bouquet.ui.SectionHeading
import dev.rose.bouquet.ui.theme.LocalRoseTheme
import dev.rose.bouquet.ui.theme.RoseTheme
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * The visualiser, with the desktop app's shapes.
 *
 * All 21 shapes, the four colour modes and the five motions, drawn on a Compose
 * canvas from [Spectrum] rather than cava. Shapes can be layered — that is what
 * makes it yours rather than a menu of presets — so [VisualiserCanvas] takes a
 * list and paints them back to front.
 */
enum class Shape(val key: String, val label: String) {
    Wave("wave", "Wave"),
    Bars("bars", "Bars"),
    Mirror("mirror", "Mirror"),
    Line("line", "Line"),
    Blocks("blocks", "Blocks"),
    Dots("dots", "Dots"),
    Radial("radial", "Radial"),
    RadialBars("radial-bars", "Radial bars"),
    RadialDots("radial-dots", "Radial dots"),
    RadialLines("radial-lines", "Radial lines"),
    RadialMirror("radial-mirror", "Radial mirror"),
    RadialBloom("radial-bloom", "Radial bloom"),
    Turntable("turntable", "Turntable"),
    Ribbons("ribbons", "Ribbons"),
    Tunnel("tunnel", "Tunnel"),
    Starfield("starfield", "Starfield"),
    BarsTop("bars-top", "Bars (top)"),
    BlocksTop("blocks-top", "Blocks (top)"),
    DotsTop("dots-top", "Dots (top)"),
    WaveTop("wave-top", "Wave (top)"),
    LineTop("line-top", "Line (top)"),
    ;

    /** Whether this shape hangs from the top edge rather than standing on the bottom. */
    val fromTop: Boolean get() = key.endsWith("-top")

    val radial: Boolean get() = key.startsWith("radial") || this == Turntable || this == Tunnel
}

enum class ColourMode(val key: String, val label: String) {
    Theme("theme", "Theme"),
    Solid("solid", "Solid"),
    Multi("multi", "Multi"),
    Rainbow("rainbow", "Rainbow"),
}

enum class ColourMotion(val key: String, val label: String) {
    Static("static", "Static"),
    Fade("fade", "Fade"),
    Sweep("sweep", "Sweep"),
    Flash("flash", "Flash"),
    Pulse("pulse", "Pulse"),
}

/** One drawn layer: a shape, how big, and how it is coloured. */
data class Layer(
    val shape: Shape,
    val scale: Float = 1f,
    val mode: ColourMode = ColourMode.Theme,
    val motion: ColourMotion = ColourMotion.Static,
)

@UnstableApi
@Composable
fun VisualiserScreen(model: AppViewModel) {
    val theme = LocalRoseTheme.current
    val context = LocalContext.current
    val settings by model.settings.collectAsStateWithLifecycle()

    val spectrum = remember { Spectrum() }
    var granted by remember {
        mutableStateOf(
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                PackageManager.PERMISSION_GRANTED
        )
    }

    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        granted = it
        if (it) spectrum.start()
    }

    DisposableEffect(granted) {
        if (granted) spectrum.start()
        onDispose { spectrum.stop() }
    }

    val layers = settings.visualiserLayers
    var intensity by remember(settings.visualiserIntensity) {
        mutableStateOf(settings.visualiserIntensity)
    }

    Column(Modifier.fillMaxSize()) {
        Box(
            Modifier
                .fillMaxWidth()
                .padding(16.dp)
                .background(theme.panel)
                .size(width = 0.dp, height = 220.dp)
                .fillMaxWidth(),
        ) {
            if (granted) {
                VisualiserCanvas(
                    bands = spectrum.bands.value,
                    layers = layers,
                    theme = theme,
                    intensity = intensity,
                    modifier = Modifier.fillMaxSize(),
                )
            } else {
                Column(
                    Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("The visualiser needs permission to read audio",
                        color = theme.text, style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.size(6.dp))
                    Text(
                        "Android counts reading this app's own output as recording. " +
                            "Nothing is stored or sent, and refusing costs only the visualiser.",
                        color = theme.textDim, style = MaterialTheme.typography.bodySmall,
                        textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                        modifier = Modifier.padding(horizontal = 24.dp),
                    )
                    Spacer(Modifier.size(10.dp))
                    Button(onClick = { ask.launch(Manifest.permission.RECORD_AUDIO) }) {
                        Text("Allow")
                    }
                }
            }
        }

        if (granted && !spectrum.active.value) {
            Text(
                "This device would not attach a visualiser to the audio output. " +
                    "That happens on some phones and some Bluetooth routes.",
                color = theme.textDim,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(horizontal = 16.dp),
            )
        }

        SectionHeading("Reactivity")
        Slider(
            value = intensity,
            onValueChange = { intensity = it },
            onValueChangeFinished = { model.setVisualiserIntensity(intensity) },
            valueRange = 0.2f..3f,
            modifier = Modifier.padding(horizontal = 16.dp),
        )

        SectionHeading("Shapes") {
            Text(
                if (layers.isEmpty()) "none" else "${layers.size} layered",
                color = theme.textDim, style = MaterialTheme.typography.labelSmall,
            )
        }
        Text(
            "Tap to add or remove. Several at once layer over each other.",
            color = theme.textDim,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(horizontal = 16.dp),
        )
        LazyRow(
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(Shape.entries.toList()) { shape ->
                val on = layers.any { it.shape == shape }
                FilterChip(
                    selected = on,
                    onClick = {
                        model.setVisualiserLayers(
                            if (on) layers.filterNot { it.shape == shape }
                            else layers + Layer(shape)
                        )
                    },
                    label = { Text(shape.label) },
                )
            }
        }

        SectionHeading("Colour")
        LazyRow(
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(ColourMode.entries.toList()) { mode ->
                FilterChip(
                    selected = layers.firstOrNull()?.mode == mode,
                    onClick = { model.setVisualiserLayers(layers.map { it.copy(mode = mode) }) },
                    label = { Text(mode.label) },
                )
            }
        }
        LazyRow(
            contentPadding = androidx.compose.foundation.layout.PaddingValues(16.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(ColourMotion.entries.toList()) { motion ->
                FilterChip(
                    selected = layers.firstOrNull()?.motion == motion,
                    onClick = { model.setVisualiserLayers(layers.map { it.copy(motion = motion) }) },
                    label = { Text(motion.label) },
                )
            }
        }
    }
}

/**
 * Paints the layers.
 *
 * Back to front, in the order they were added, so a wave behind bars behaves
 * the way stacking one on top of the other suggests it will.
 */
@Composable
fun VisualiserCanvas(
    bands: FloatArray,
    layers: List<Layer>,
    theme: RoseTheme,
    intensity: Float,
    modifier: Modifier = Modifier,
    phase: Float = 0f,
) {
    Canvas(modifier) {
        layers.forEach { layer -> drawLayer(layer, bands, theme, intensity, phase) }
    }
}

private fun DrawScope.drawLayer(
    layer: Layer,
    bands: FloatArray,
    theme: RoseTheme,
    intensity: Float,
    phase: Float,
) {
    if (bands.isEmpty()) return

    val level = { index: Int -> (bands[index] * intensity * layer.scale).coerceIn(0f, 1f) }
    val colourAt = { index: Int -> colourFor(layer, index, bands.size, theme, phase, level(index)) }

    if (layer.shape.radial) drawRadial(layer, bands, level, colourAt, theme)
    else drawLinear(layer, bands, level, colourAt)
}

// ── Linear shapes ─────────────────────────────────────────────────

private fun DrawScope.drawLinear(
    layer: Layer,
    bands: FloatArray,
    level: (Int) -> Float,
    colourAt: (Int) -> Color,
) {
    val count = bands.size
    val step = size.width / count
    val baseline = if (layer.shape.fromTop) 0f else size.height
    val direction = if (layer.shape.fromTop) 1f else -1f

    when (layer.shape) {
        Shape.Bars, Shape.BarsTop -> for (i in 0 until count) {
            val height = level(i) * size.height
            drawRect(
                color = colourAt(i),
                topLeft = Offset(i * step, if (layer.shape.fromTop) 0f else size.height - height),
                size = androidx.compose.ui.geometry.Size(step * 0.72f, height),
            )
        }

        Shape.Blocks, Shape.BlocksTop -> for (i in 0 until count) {
            // Quantised into discrete cells — the segmented look of a hardware
            // level meter rather than a smooth bar.
            val cells = (level(i) * BLOCK_ROWS).toInt()
            val cell = size.height / BLOCK_ROWS
            for (row in 0 until cells) {
                val y = if (layer.shape.fromTop) row * cell else size.height - (row + 1) * cell
                drawRect(
                    color = colourAt(i),
                    topLeft = Offset(i * step, y + cell * 0.15f),
                    size = androidx.compose.ui.geometry.Size(step * 0.72f, cell * 0.7f),
                )
            }
        }

        Shape.Dots, Shape.DotsTop -> for (i in 0 until count) {
            val y = baseline + direction * level(i) * size.height
            drawCircle(colourAt(i), radius = step * 0.3f, center = Offset(i * step + step / 2, y))
        }

        Shape.Wave, Shape.WaveTop, Shape.Line, Shape.LineTop -> {
            val path = Path()
            for (i in 0 until count) {
                val x = i * step + step / 2
                val y = baseline + direction * level(i) * size.height
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            val filled = layer.shape == Shape.Wave || layer.shape == Shape.WaveTop
            if (filled) {
                path.lineTo(size.width, baseline)
                path.lineTo(0f, baseline)
                path.close()
                drawPath(path, colourAt(count / 2).copy(alpha = 0.55f))
            } else {
                drawPath(path, colourAt(count / 2), style = Stroke(width = 3.dp.toPx(),
                    cap = StrokeCap.Round))
            }
        }

        Shape.Mirror -> for (i in 0 until count) {
            // Grown from the middle in both directions.
            val half = level(i) * size.height / 2
            drawRect(
                color = colourAt(i),
                topLeft = Offset(i * step, size.height / 2 - half),
                size = androidx.compose.ui.geometry.Size(step * 0.72f, half * 2),
            )
        }

        Shape.Ribbons -> {
            val path = Path()
            for (i in 0 until count) {
                val x = i * step + step / 2
                val y = size.height / 2 + sin(i * 0.4f) * level(i) * size.height / 2
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            drawPath(path, colourAt(count / 2), style = Stroke(width = 6.dp.toPx(),
                cap = StrokeCap.Round))
        }

        Shape.Starfield -> for (i in 0 until count) {
            val radius = level(i) * size.minDimension * 0.4f
            val angle = i * (2 * PI / count)
            drawCircle(
                colourAt(i),
                radius = (level(i) * 6f).coerceAtLeast(1f),
                center = Offset(
                    size.width / 2 + (cos(angle) * radius).toFloat(),
                    size.height / 2 + (sin(angle) * radius).toFloat(),
                ),
            )
        }

        else -> Unit    // radial shapes are handled elsewhere
    }
}

// ── Radial shapes ─────────────────────────────────────────────────

private fun DrawScope.drawRadial(
    layer: Layer,
    bands: FloatArray,
    level: (Int) -> Float,
    colourAt: (Int) -> Color,
    theme: RoseTheme,
) {
    val count = bands.size
    val centre = Offset(size.width / 2, size.height / 2)
    val inner = size.minDimension * 0.18f
    val reach = size.minDimension * 0.32f

    when (layer.shape) {
        Shape.RadialBars, Shape.Radial -> for (i in 0 until count) {
            val angle = i * (2 * PI / count)
            val length = inner + level(i) * reach
            drawLine(
                colourAt(i),
                start = centre + Offset((cos(angle) * inner).toFloat(), (sin(angle) * inner).toFloat()),
                end = centre + Offset((cos(angle) * length).toFloat(), (sin(angle) * length).toFloat()),
                strokeWidth = if (layer.shape == Shape.Radial) 2.dp.toPx() else 5.dp.toPx(),
                cap = StrokeCap.Round,
            )
        }

        Shape.RadialDots -> for (i in 0 until count) {
            val angle = i * (2 * PI / count)
            val length = inner + level(i) * reach
            drawCircle(
                colourAt(i), radius = 3.dp.toPx(),
                center = centre + Offset((cos(angle) * length).toFloat(),
                    (sin(angle) * length).toFloat()),
            )
        }

        Shape.RadialLines -> for (i in 0 until count) {
            val angle = i * (2 * PI / count)
            val length = inner + level(i) * reach
            drawLine(
                colourAt(i), start = centre,
                end = centre + Offset((cos(angle) * length).toFloat(),
                    (sin(angle) * length).toFloat()),
                strokeWidth = 1.5.dp.toPx(),
            )
        }

        Shape.RadialMirror -> for (i in 0 until count) {
            val angle = i * (PI / count)      // half turn, mirrored
            val length = inner + level(i) * reach
            listOf(angle, angle + PI).forEach { a ->
                drawLine(
                    colourAt(i),
                    start = centre + Offset((cos(a) * inner).toFloat(), (sin(a) * inner).toFloat()),
                    end = centre + Offset((cos(a) * length).toFloat(), (sin(a) * length).toFloat()),
                    strokeWidth = 5.dp.toPx(), cap = StrokeCap.Round,
                )
            }
        }

        Shape.RadialBloom -> {
            val path = Path()
            for (i in 0..count) {
                val index = i % count
                val angle = index * (2 * PI / count)
                val length = inner + level(index) * reach
                val point = centre + Offset((cos(angle) * length).toFloat(),
                    (sin(angle) * length).toFloat())
                if (i == 0) path.moveTo(point.x, point.y) else path.lineTo(point.x, point.y)
            }
            path.close()
            drawPath(path, colourAt(0).copy(alpha = 0.5f))
            drawPath(path, colourAt(count / 2), style = Stroke(width = 2.dp.toPx()))
        }

        Shape.Tunnel -> for (ring in 1..TUNNEL_RINGS) {
            val index = (ring * count / TUNNEL_RINGS).coerceIn(0, count - 1)
            drawCircle(
                colourAt(index).copy(alpha = 0.6f),
                radius = inner + ring * (reach / TUNNEL_RINGS) + level(index) * 20f,
                center = centre,
                style = Stroke(width = 2.dp.toPx()),
            )
        }

        Shape.Turntable -> {
            // The record: a disc that grows with the low end, with the label
            // left clear for album art to sit in.
            val bass = level(1)
            drawCircle(theme.elevated, radius = inner + reach * 0.9f, center = centre)
            drawCircle(
                colourAt(1).copy(alpha = 0.8f),
                radius = inner + reach * 0.9f, center = centre,
                style = Stroke(width = (2 + bass * 8).dp.toPx()),
            )
            for (ring in 1..6) {
                drawCircle(
                    theme.border.copy(alpha = 0.6f),
                    radius = inner + ring * (reach * 0.14f),
                    center = centre, style = Stroke(width = 1.dp.toPx()),
                )
            }
            drawCircle(colourAt(0), radius = inner * 0.55f, center = centre)
        }

        else -> Unit
    }
}

// ── Colour ────────────────────────────────────────────────────────

/**
 * The colour of one band.
 *
 * Mode decides where the colour comes from, motion decides how it changes over
 * time. They compose: rainbow + sweep is a moving rainbow, solid + pulse is one
 * colour breathing with the music.
 */
private fun colourFor(
    layer: Layer,
    index: Int,
    count: Int,
    theme: RoseTheme,
    phase: Float,
    level: Float,
): Color {
    val position = index.toFloat() / count.coerceAtLeast(1)

    val base = when (layer.mode) {
        ColourMode.Theme -> theme.accent
        ColourMode.Solid -> theme.accent
        ColourMode.Multi -> lerp(theme.accent, theme.success, position)
        ColourMode.Rainbow -> Color.hsv(((position + phase) % 1f) * 360f, 0.7f, 1f)
    }

    return when (layer.motion) {
        ColourMotion.Static -> base
        ColourMotion.Fade -> base.copy(alpha = 0.35f + level * 0.65f)
        ColourMotion.Sweep -> {
            val distance = kotlin.math.abs(position - (phase % 1f))
            base.copy(alpha = (1f - distance).coerceIn(0.25f, 1f))
        }
        ColourMotion.Flash -> if (level > 0.7f) Color.White else base
        ColourMotion.Pulse -> base.copy(alpha = (0.4f + level).coerceAtMost(1f))
    }
}

private fun lerp(from: Color, to: Color, t: Float) = Color(
    red = from.red + (to.red - from.red) * t,
    green = from.green + (to.green - from.green) * t,
    blue = from.blue + (to.blue - from.blue) * t,
    alpha = 1f,
)

private const val BLOCK_ROWS = 14
private const val TUNNEL_RINGS = 8
