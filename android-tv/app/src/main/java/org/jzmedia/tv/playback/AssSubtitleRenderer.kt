package org.jzmedia.tv.playback

import io.github.peerless2012.ass.Ass
import io.github.peerless2012.ass.AssFrame
import io.github.peerless2012.ass.AssRender
import io.github.peerless2012.ass.AssTexType
import io.github.peerless2012.ass.AssTrack

/** Owns one libass library; rendering and disposal share the same native lock. */
class AssSubtitleRenderer : AssSubtitleEngine {
    private val library = Ass()
    private var track: AssTrack? = null
    private var renderer: AssRender? = null
    private var lastFrame: AssFrame? = null
    private var closed = false
    private var frameWidth = 0
    private var frameHeight = 0
    private var storageWidth = 0
    private var storageHeight = 0
    private var fontScale = 0f

    override fun addFont(name: String, bytes: ByteArray) = library.lock.withNativeLock {
        check(!closed) { "Subtitle renderer is closed" }
        library.addFont(name, bytes)
        // libass discovers attached fonts when ass_set_fonts initializes a renderer.
        // Rebuild it if a font arrives after loading the track.
        if (renderer != null) createRenderer()
    }

    override fun load(source: String) = library.lock.withNativeLock {
        check(!closed) { "Subtitle renderer is closed" }
        val lines = source.removePrefix("\uFEFF").lineSequence().map(String::trim).toList()
        require(lines.any { it.equals("[Script Info]", ignoreCase = true) } &&
            lines.any { it.equals("[Events]", ignoreCase = true) }) { "Invalid ASS subtitle document" }
        val hasDialogue = lines.any { it.startsWith("Dialogue:", ignoreCase = true) }
        val replacement = library.createTrack()
        try {
            replacement.readBuffer(source.toByteArray(Charsets.UTF_8))
            check(!hasDialogue || !replacement.getEvents().isNullOrEmpty()) { "Unable to parse ASS subtitle events" }
            renderer?.setTrack(null)
            track?.release()
            track = replacement
            createRenderer()
        } catch (error: RuntimeException) {
            if (track !== replacement) replacement.release()
            throw error
        } catch (error: LinkageError) {
            if (track !== replacement) replacement.release()
            throw error
        }
    }

    private fun createRenderer() {
        renderer?.release()
        renderer = null
        lastFrame = null
        frameWidth = 0
        frameHeight = 0
        storageWidth = 0
        storageHeight = 0
        fontScale = 0f
        renderer = library.createRender().apply {
            setCacheLimit(10_000, 32)
            setTrack(track)
        }
    }

    /** [sourceMillis] is the original video's timeline, including session and user offsets. */
    fun render(
        sourceMillis: Long,
        width: Int,
        height: Int,
        storageWidth: Int,
        storageHeight: Int,
        fontScale: Float,
    ): AssFrame? = library.lock.withNativeLock {
        if (closed || width <= 0 || height <= 0) return@withNativeLock null
        val active = renderer ?: return@withNativeLock null
        val sourceWidth = storageWidth.takeIf { it > 0 } ?: track?.getWidth()?.takeIf { it > 0 } ?: width
        val sourceHeight = storageHeight.takeIf { it > 0 } ?: track?.getHeight()?.takeIf { it > 0 } ?: height
        if (this.storageWidth != sourceWidth || this.storageHeight != sourceHeight) {
            active.setStorageSize(sourceWidth, sourceHeight)
            this.storageWidth = sourceWidth
            this.storageHeight = sourceHeight
        }
        if (frameWidth != width || frameHeight != height) {
            active.setFrameSize(width, height)
            frameWidth = width
            frameHeight = height
        }
        val scale = fontScale.takeIf { it.isFinite() && it > 0f } ?: 1f
        if (this.fontScale != scale) {
            active.setFontScale(scale)
            this.fontScale = scale
        }
        val result = active.renderFrame(sourceMillis.coerceAtLeast(0), AssTexType.BITMAP_ALPHA)
        if (result == null) {
            lastFrame = null
            return@withNativeLock null
        }
        if (result.changed == 0) {
            // JNI omits unchanged images. Preserve them for a newly attached view too.
            return@withNativeLock AssFrame(lastFrame?.images, 0)
        }
        lastFrame = result
        result
    }

    override fun close() = library.lock.withNativeLock {
        if (closed) return@withNativeLock
        closed = true
        renderer?.setTrack(null)
        renderer?.release()
        renderer = null
        track?.release()
        track = null
        lastFrame = null
        library.release()
    }

    companion object {
        private val available: Boolean by lazy {
            try {
                Ass().release()
                true
            } catch (_: LinkageError) {
                false
            } catch (_: RuntimeException) {
                false
            }
        }

        fun isAvailable(): Boolean = available
    }
}

private inline fun <T> java.util.concurrent.locks.ReentrantLock.withNativeLock(action: () -> T): T {
    lock()
    try {
        return action()
    } finally {
        unlock()
    }
}
