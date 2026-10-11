package org.jzmedia.tv.playback

import android.graphics.Bitmap
import io.github.peerless2012.ass.AssFrame
import io.github.peerless2012.ass.AssTex
import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Device tests exercise the packaged libass JNI, rather than a subtitle parser mock. */
class AssSubtitleRendererTest {
    @Test fun fontBytesCanBeLoadedBeforeAndAfterTheAssTrack() {
        val font = File("/system/fonts").listFiles().orEmpty().firstOrNull {
            it.isFile && it.extension.lowercase() in setOf("ttf", "otf", "ttc")
        }
        assertNotNull("A system font is required for the font attachment test", font)
        val bytes = font!!.readBytes()
        AssSubtitleRenderer().use { renderer ->
            renderer.addFont(font.name, bytes)
            renderer.load(document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello"))
            assertFalse(renderer.renderAt(1500)!!.images.isNullOrEmpty())
            renderer.addFont(font.name, bytes)
            assertFalse(renderer.renderAt(1500)!!.images.isNullOrEmpty())
        }
    }

    @Test fun systemFontsPreserveColorsPositionAndSourceTimeline() {
        assertTrue(AssSubtitleRenderer.isAvailable())
        AssSubtitleRenderer().use { renderer ->
            renderer.load(document("""
                Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\an7\pos(80,40)\1c&H0000FF&}TOP
                Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\1c&HFF0000&}ONE\NTWO
            """))
            assertNull(renderer.renderAt(0))
            val shown = renderer.renderAt(1500)
            assertNotNull(shown)
            val red = shown!!.images.orEmpty().filter { it.red() > 200 && it.blue() < 20 }
            val blue = shown.images.orEmpty().filter { it.blue() > 200 && it.red() < 20 }
            assertFalse(red.isEmpty())
            assertFalse(blue.isEmpty())
            assertTrue(red.all { it.y in 35..100 })
            assertTrue(blue.all { it.y > 240 })
            assertTrue(blue.maxOf { it.y + it.h } - blue.minOf { it.y } > 30)
            assertTrue(shown.images.orEmpty().all { it.bitmap == null || it.bitmap!!.config == Bitmap.Config.ALPHA_8 })
            val unchanged = renderer.renderAt(1500)
            assertEquals(0, unchanged!!.changed)
            assertSame(shown.images, unchanged.images)
            assertNull(renderer.renderAt(3500))
            // Seeking backwards uses the original timeline, including a nonzero HLS start.
            assertFalse(renderer.renderAt(1500)!!.images.isNullOrEmpty())
        }
    }

    @Test fun vectorDrawingAndMoveAnimationRenderAtTheCurrentSourceTime() {
        AssSubtitleRenderer().use { renderer ->
            renderer.load(document("""
                Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\an7\move(40,60,140,60,0,2000)\1c&H0000FF&\p1}m 0 0 l 24 0 24 16 0 16
            """))
            val early = renderer.renderAt(1250)!!.images.orEmpty().filter { it.bitmap != null }
            val late = renderer.renderAt(2750)!!.images.orEmpty().filter { it.bitmap != null }
            assertFalse(early.isEmpty())
            assertFalse(late.isEmpty())
            assertTrue(late.minOf { it.x } - early.minOf { it.x } > 60)
            assertTrue(early.all { it.red() > 200 && it.y in 55..65 })
            assertNull(renderer.renderAt(3001))
        }
    }

    @Test fun invalidDocumentsFailAndDoubleCloseCannotUseFreedNativeMemory() {
        val renderer = AssSubtitleRenderer()
        try {
            expectFailure { renderer.load("not an ASS subtitle") }
            expectFailure { renderer.load(document("Dialogue: this is malformed")) }
            renderer.load(document(""))
            assertNull(renderer.renderAt(1500))
            renderer.load(document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello"))
            assertNotNull(renderer.renderAt(1500))
        } finally {
            renderer.close()
            renderer.close()
        }
        assertNull(renderer.renderAt(1500))
        expectFailure { renderer.load(document("")) }
        expectFailure { renderer.addFont("unused.ttf", byteArrayOf()) }
    }

    @Test fun subtitleSizeChangesTheNativeGlyphs() {
        AssSubtitleRenderer().use { renderer ->
            // libass keeps explicitly positioned signs at their authored size.
            renderer.load(document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello"))
            val small = renderer.render(1500, 640, 360, 640, 360, .8f)!!.images.orEmpty()
            val large = renderer.render(1500, 640, 360, 640, 360, 1.2f)!!.images.orEmpty()
            assertTrue(large.maxOf { it.x + it.w } - large.minOf { it.x } > small.maxOf { it.x + it.w } - small.minOf { it.x })
        }
    }

    @Test fun positionedTextKeepsItsAuthoredSizeWhenThePreferenceChanges() {
        AssSubtitleRenderer().use { renderer ->
            renderer.load(document("Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an7\\pos(80,40)}Hello"))
            val small = renderer.render(1500, 640, 360, 640, 360, .8f)!!.images.orEmpty()
            val large = renderer.render(1500, 640, 360, 640, 360, 1.2f)!!.images.orEmpty()
            assertEquals(small.map { listOf(it.x, it.y, it.w, it.h) }, large.map { listOf(it.x, it.y, it.w, it.h) })
        }
    }

    private fun AssSubtitleRenderer.renderAt(sourceMillis: Long): AssFrame? =
        render(sourceMillis, 640, 360, 640, 360, 1f)

    private fun AssTex.red(): Int = color ushr 24 and 255
    private fun AssTex.blue(): Int = color ushr 8 and 255

    private fun expectFailure(action: () -> Unit) {
        try {
            action()
            fail("Expected the invalid operation to fail")
        } catch (_: IllegalStateException) {
            // Expected lifecycle/native parsing rejection.
        } catch (_: IllegalArgumentException) {
            // Expected document validation rejection.
        }
    }

    private fun document(events: String): String = """
        [Script Info]
        ScriptType: v4.00+
        PlayResX: 640
        PlayResY: 360
        ScaledBorderAndShadow: yes
        [V4+ Styles]
        Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        Style: Default,sans-serif,28,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,20,20,20,1
        [Events]
        Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
    """.trimIndent() + "\n" + events.trimIndent() + "\n"
}
