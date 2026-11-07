package org.jzmedia.tv.playback

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackConfirmPressTest {
    // Plain key identifiers keep this state-machine test independent of Android KeyEvent.
    private val center = 23
    private val enter = 66
    private val right = 22

    @Test fun ordinaryButtonPressesPassThroughUntilTheParentHandlesThem() {
        val press = PlaybackConfirmPress()

        assertFalse(press.consume(center, released = false))
        assertFalse(press.consume(center, released = true))
        assertFalse(press.consume(enter, released = false))
        assertFalse(press.consume(enter, released = true))
    }

    @Test fun handledConfirmConsumesItsMatchingReleaseOnlyOnce() {
        for (key in listOf(center, enter)) {
            val press = PlaybackConfirmPress()
            press.handle(key)

            assertTrue(press.consume(key, released = true))
            assertFalse(press.consume(key, released = true))
        }
    }

    @Test fun heldConfirmRepeatsCannotClickTheButtonThatWasJustRevealed() {
        val press = PlaybackConfirmPress()
        press.handle(center)

        repeat(5) { assertTrue(press.consume(center, released = false)) }
        assertTrue(press.consume(center, released = true))
        assertFalse(press.consume(center, released = false))
    }

    @Test fun unrelatedKeysPassThroughWithoutDiscardingTheHandledConfirm() {
        val press = PlaybackConfirmPress()
        press.handle(center)

        for (key in listOf(right, enter)) {
            assertFalse(press.consume(key, released = false))
            assertFalse(press.consume(key, released = true))
        }
        assertTrue(press.consume(center, released = false))
        assertTrue(press.consume(center, released = true))
    }

    @Test fun aNewPhysicalPressCanActivateTheFocusedButtonOrBeHandledAgain() {
        val press = PlaybackConfirmPress()
        press.handle(center)
        assertTrue(press.consume(center, released = true))

        // The following down/up reaches the now-focused control normally.
        assertFalse(press.consume(center, released = false))
        assertFalse(press.consume(center, released = true))

        // After hiding controls again, the parent can accept another reveal press.
        press.handle(center)
        assertTrue(press.consume(center, released = false))
        assertTrue(press.consume(center, released = true))
    }
}
