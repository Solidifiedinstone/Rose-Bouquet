package dev.rose.bouquet.player

import android.content.Intent
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService

/**
 * Playback that outlives the screen.
 *
 * This service is the reason the Android client is a native app rather than a
 * web page: a music player has to keep playing when the app is not on screen,
 * show up in the notification shade and on the lock screen, respond to headset
 * buttons and to Bluetooth controls in a car, and duck out of the way when a
 * navigation prompt speaks. Media3's `MediaSessionService` gets all of that for
 * the price of declaring it properly.
 *
 * `setHandleAudioBecomingNoisy` is the one people notice when it is missing:
 * without it, yanking the headphones out plays your music to the room.
 */
class PlaybackService : MediaSessionService() {

    private var player: ExoPlayer? = null
    private var session: MediaSession? = null

    override fun onCreate() {
        super.onCreate()

        val exo = ExoPlayer.Builder(this)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(C.USAGE_MEDIA)
                    .setContentType(C.AUDIO_CONTENT_TYPE_MUSIC)
                    .build(),
                /* handleAudioFocus = */ true,
            )
            .setHandleAudioBecomingNoisy(true)
            .build()

        player = exo
        session = MediaSession.Builder(this, exo).build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    /**
     * Stop when the user swipes the app away *and* nothing is playing.
     *
     * Killing playback on a swipe-away is a common and infuriating bug: people
     * dismiss the task card while music is playing and expect the music to
     * carry on, because the notification is still there offering to pause it.
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        val playing = player?.playWhenReady == true && player?.mediaItemCount != 0
        if (!playing) {
            stopSelf()
        }
    }

    override fun onDestroy() {
        session?.run {
            player.release()
            release()
        }
        session = null
        player = null
        super.onDestroy()
    }
}
