package org.jzmedia.tv.playback

/** Retain a handled confirm press until release, even if it reveals a newly focused button. */
internal class PlaybackConfirmPress {
    private var handledKey: Int? = null

    fun handle(keyCode: Int) { handledKey = keyCode }

    fun consume(keyCode: Int, released: Boolean): Boolean {
        if (handledKey != keyCode) return false
        if (released) handledKey = null
        return true
    }
}
