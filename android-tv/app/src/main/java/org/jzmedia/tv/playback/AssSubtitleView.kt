package org.jzmedia.tv.playback

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.view.Choreographer
import android.view.View
import io.github.peerless2012.ass.AssFrame
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import kotlin.math.roundToInt
import kotlin.math.sqrt

/** Transparent subtitle layer. Player state is sampled on the UI thread; libass runs on a worker. */
class AssSubtitleView(context: Context) : View(context), Choreographer.FrameCallback {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG)
    private val bitmapBounds = RectF()
    private var renderer: AssSubtitleRenderer? = null
    private var sourceTime: () -> Long = { 0 }
    private var onError: () -> Unit = {}
    private var videoAspectRatio = 0f
    private var storageWidth = 0
    private var storageHeight = 0
    private var fontScale = 1f
    private var worker: ExecutorService? = null
    private var generation = 0L
    private var rendering = false
    private var scheduled = false
    private var failed = false
    private var released = false
    private var frame: AssFrame? = null
    private var displayedWidth = 0
    private var displayedHeight = 0
    private var displayedBounds = RectF()
    private var lastTime: Long? = null

    init {
        isFocusable = false
        isClickable = false
        importantForAccessibility = IMPORTANT_FOR_ACCESSIBILITY_NO
        setWillNotDraw(false)
    }

    fun bind(
        renderer: AssSubtitleRenderer?,
        videoAspectRatio: Float,
        sourceTime: () -> Long,
        fontScale: Float,
        onError: () -> Unit,
        storageWidth: Int = 0,
        storageHeight: Int = 0,
    ) {
        if (released) return
        this.sourceTime = sourceTime
        this.onError = onError
        val changed = this.renderer !== renderer || this.videoAspectRatio != videoAspectRatio ||
            this.fontScale != fontScale || this.storageWidth != storageWidth || this.storageHeight != storageHeight
        if (changed) {
            generation++
            rendering = false
            failed = false
            lastTime = null
            frame = null
            this.renderer = renderer
            this.videoAspectRatio = videoAspectRatio
            this.fontScale = fontScale
            this.storageWidth = storageWidth
            this.storageHeight = storageHeight
            invalidate()
        }
        scheduleFrame()
    }

    override fun onAttachedToWindow() {
        super.onAttachedToWindow()
        if (!released) {
            worker = newWorker()
            scheduleFrame()
        }
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        generation++
        rendering = false
        lastTime = null
        frame = null
        invalidate()
        scheduleFrame()
    }

    private fun scheduleFrame() {
        if (!released && !failed && isAttachedToWindow && renderer != null && !scheduled) {
            scheduled = true
            Choreographer.getInstance().postFrameCallback(this)
        }
    }

    override fun doFrame(frameTimeNanos: Long) {
        scheduled = false
        if (released || failed || !isAttachedToWindow) return
        val active = renderer ?: return
        val time = try {
            sourceTime()
        } catch (_: RuntimeException) {
            fail()
            return
        }
        if (time < 0) {
            // A controller has no source clock while preparing/replacing a media item.
            if (lastTime != time || frame != null) {
                generation++
                rendering = false
                frame = null
                lastTime = time
                invalidate()
            }
            scheduleFrame()
            return
        }
        if (!rendering && width > 0 && height > 0) {
            if (lastTime != time) {
                val bounds = videoBounds()
                val scale = minOf(1.0, sqrt(MAX_RENDER_PIXELS / (bounds.width().toDouble() * bounds.height())))
                val renderWidth = (bounds.width() * scale).roundToInt().coerceAtLeast(1)
                val renderHeight = (bounds.height() * scale).roundToInt().coerceAtLeast(1)
                val taskGeneration = generation
                val sourceWidth = storageWidth
                val sourceHeight = storageHeight
                val size = fontScale
                rendering = true
                lastTime = time
                try {
                    worker?.execute {
                        try {
                            val result = active.render(time, renderWidth, renderHeight, sourceWidth, sourceHeight, size)
                            post {
                                if (released || !isAttachedToWindow || taskGeneration != generation || renderer !== active) return@post
                                rendering = false
                                displayedWidth = renderWidth
                                displayedHeight = renderHeight
                                displayedBounds = bounds
                                if (result == null || result.changed != 0 || frame == null) {
                                    frame = result
                                    invalidate()
                                }
                            }
                        } catch (_: RuntimeException) {
                            post { if (taskGeneration == generation && renderer === active) fail() }
                        } catch (_: LinkageError) {
                            post { if (taskGeneration == generation && renderer === active) fail() }
                        }
                    }
                } catch (_: RejectedExecutionException) {
                    rendering = false
                    lastTime = null
                }
            }
        }
        scheduleFrame()
    }

    private fun videoBounds(): RectF {
        val fallback = width.toFloat() / height
        val aspect = videoAspectRatio.takeIf { it.isFinite() && it > 0f } ?: fallback
        val videoWidth = minOf(width.toFloat(), height * aspect)
        val videoHeight = videoWidth / aspect
        val left = (width - videoWidth) / 2f
        val top = (height - videoHeight) / 2f
        return RectF(left, top, left + videoWidth, top + videoHeight)
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if (displayedWidth <= 0 || displayedHeight <= 0) return
        val save = canvas.save()
        canvas.clipRect(displayedBounds)
        val scaleX = displayedBounds.width() / displayedWidth
        val scaleY = displayedBounds.height() / displayedHeight
        frame?.images?.forEach { image ->
            image.bitmap?.let { bitmap ->
                val red = image.color ushr 24 and 255
                val green = image.color ushr 16 and 255
                val blue = image.color ushr 8 and 255
                val alpha = 255 - (image.color and 255)
                paint.color = (alpha shl 24) or (red shl 16) or (green shl 8) or blue
                bitmapBounds.set(
                    displayedBounds.left + image.x * scaleX,
                    displayedBounds.top + image.y * scaleY,
                    displayedBounds.left + (image.x + bitmap.width) * scaleX,
                    displayedBounds.top + (image.y + bitmap.height) * scaleY,
                )
                canvas.drawBitmap(bitmap, null, bitmapBounds, paint)
            }
        }
        canvas.restoreToCount(save)
    }

    private fun fail() {
        if (released || failed || !isAttachedToWindow) return
        failed = true
        rendering = false
        frame = null
        Choreographer.getInstance().removeFrameCallback(this)
        scheduled = false
        invalidate()
        onError()
    }

    private fun stopRendering() {
        generation++
        Choreographer.getInstance().removeFrameCallback(this)
        scheduled = false
        rendering = false
        lastTime = null
        frame = null
        worker?.shutdownNow()
        worker = null
        invalidate()
    }

    override fun onDetachedFromWindow() {
        stopRendering()
        super.onDetachedFromWindow()
    }

    /** The controller owns the renderer; releasing a view only cancels its rendering work. */
    fun release() {
        if (released) return
        released = true
        stopRendering()
        renderer = null
        sourceTime = { 0 }
        onError = {}
    }

    private fun newWorker(): ExecutorService = Executors.newSingleThreadExecutor { task ->
        Thread(task, "JzAssRender").apply { isDaemon = true }
    }

    companion object {
        private const val MAX_RENDER_PIXELS = 1920.0 * 1080
    }
}
