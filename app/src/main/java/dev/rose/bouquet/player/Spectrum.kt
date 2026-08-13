package dev.rose.bouquet.player

import android.media.audiofx.Visualizer
import androidx.compose.runtime.mutableStateOf
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.log10
import kotlin.math.pow

/**
 * The audio spectrum, for the visualiser to draw.
 *
 * This is the phone's answer to cava on the desktop. Android's own
 * [Visualizer] effect taps the output of one audio session and hands back an
 * FFT, which is the same shape of data cava produces — so the shapes ported
 * from the desktop draw from an identical input and look the same.
 *
 * Three things about it are worth knowing, because all three shaped the code:
 *
 * - **It needs `RECORD_AUDIO`.** Android treats reading an app's own output as
 *   recording, since a badly written one could be pointed at the microphone.
 *   The permission is requested only when the visualiser is first opened, and
 *   refusing it costs the visualiser and nothing else.
 * - **The capture rate is low and the resolution is coarse** — 1024 bins at
 *   best, often fewer. Smoothing matters more than precision here.
 * - **It can simply fail.** Some devices, and some audio routes, refuse to
 *   attach an effect at all. That is a flat visualiser, not an error dialog.
 */
class Spectrum(private val bandCount: Int = BANDS) {

    /** Latest levels, 0..1, low frequency first. Read from the draw loop. */
    val bands = mutableStateOf(FloatArray(bandCount))

    /** Whether audio is actually being read. False means no permission, or no device support. */
    val active = mutableStateOf(false)

    private var visualizer: Visualizer? = null
    private val smoothed = FloatArray(bandCount)

    /**
     * Start reading from an audio session.
     *
     * Session 0 is the whole output mix, which is what makes this work for
     * video as well as music without knowing which player is running.
     */
    fun start(sessionId: Int = 0) {
        if (visualizer != null) return
        runCatching {
            Visualizer(sessionId).apply {
                captureSize = Visualizer.getCaptureSizeRange()[1]
                setDataCaptureListener(
                    object : Visualizer.OnDataCaptureListener {
                        override fun onWaveFormDataCapture(v: Visualizer?, wave: ByteArray?, rate: Int) = Unit
                        override fun onFftDataCapture(v: Visualizer?, fft: ByteArray?, rate: Int) {
                            fft?.let { consume(it) }
                        }
                    },
                    Visualizer.getMaxCaptureRate() / 2,
                    /* waveform = */ false,
                    /* fft = */ true,
                )
                enabled = true
            }
        }.onSuccess {
            visualizer = it
            active.value = true
        }.onFailure {
            // No permission, or a device that will not attach the effect.
            active.value = false
        }
    }

    fun stop() {
        runCatching {
            visualizer?.enabled = false
            visualizer?.release()
        }
        visualizer = null
        active.value = false
        smoothed.fill(0f)
        bands.value = FloatArray(bandCount)
    }

    /**
     * Turn one FFT frame into the bands the shapes draw.
     *
     * Bucketed logarithmically, because pitch is logarithmic and linear buckets
     * put nearly everything audible into the leftmost two bars — the classic
     * mistake that makes a visualiser look dead below a kick drum.
     */
    private fun consume(fft: ByteArray) {
        val bins = fft.size / 2
        if (bins <= 1) return

        val out = FloatArray(bandCount)
        for (band in 0 until bandCount) {
            // Logarithmic edges across the spectrum.
            val low = binFor(band, bins)
            val high = binFor(band + 1, bins).coerceAtLeast(low + 1)

            var peak = 0f
            for (bin in low until high.coerceAtMost(bins)) {
                val real = fft[bin * 2].toFloat()
                val imaginary = fft[bin * 2 + 1].toFloat()
                val magnitude = hypot(real, imaginary)
                if (magnitude > peak) peak = magnitude
            }

            // Decibels, then normalised. Raw magnitudes are so heavily
            // weighted toward the bass that everything above it is invisible.
            val db = if (peak > 0f) 20f * log10(peak) else 0f
            out[band] = (db / DB_CEILING).coerceIn(0f, 1f)
        }

        // Asymmetric smoothing: rise quickly so a transient is visible, fall
        // slowly so the bars do not strobe. The same trade a desktop bar's
        // visualiser makes, and the reason cava's output looks calm.
        for (i in 0 until bandCount) {
            smoothed[i] = if (out[i] > smoothed[i]) {
                smoothed[i] + (out[i] - smoothed[i]) * RISE
            } else {
                smoothed[i] * FALL
            }
            if (abs(smoothed[i]) < 0.001f) smoothed[i] = 0f
        }
        bands.value = smoothed.copyOf()
    }

    private fun binFor(band: Int, bins: Int): Int {
        val fraction = band.toFloat() / bandCount
        return (bins.toFloat().pow(fraction)).toInt().coerceIn(0, bins)
    }

    companion object {
        /** Matches the desktop's cava configuration, so the shapes line up. */
        const val BANDS = 50

        private const val DB_CEILING = 45f
        private const val RISE = 0.55f
        private const val FALL = 0.86f
    }
}
