package org.jzmedia.tv.playback

import androidx.activity.ComponentActivity
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.isFocused
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.pressKey
import androidx.tv.material3.MaterialTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.jzmedia.tv.data.OnlineIdentity

/** Exercises the production control tree with a synthetic state and no media/network session. */
@OptIn(ExperimentalTestApi::class)
class OnlinePlayerInteractionTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private val state = mutableStateOf(ready())
    private val seeks = mutableListOf<Double>()
    private val skips = mutableListOf<Double>()
    private var toggles = 0
    private var retries = 0
    private var closes = 0
    private var nextCancellations = 0

    @Test fun pauseAndResumeKeepTheSameButtonFocusedAndDirectionsWorkImmediately() {
        mount()
        rule.onNodeWithText("暂停").assertIsFocused()

        repeat(3) {
            press(Key.DirectionCenter)
            rule.onNodeWithText("播放").assertIsFocused()
            press(Key.DirectionRight)
            rule.onNodeWithText("后退10秒").assertIsFocused()
            press(Key.DirectionLeft)
            rule.onNodeWithText("播放").assertIsFocused()
            press(Key.DirectionCenter)
            rule.onNodeWithText("暂停").assertIsFocused()
        }
        rule.runOnIdle { assertEquals(6, toggles) }
    }

    @Test fun okFromHiddenControlsPausesAndFocusesThePlayButton() {
        mount()
        back()
        rule.onNodeWithText("暂停").assertDoesNotExist()

        press(Key.DirectionCenter)
        rule.onNodeWithText("播放").assertIsFocused()
        rule.onNodeWithContentDescription("在线播放进度").assert(
            SemanticsMatcher.expectValue(SemanticsProperties.Focused, false))
        press(Key.DirectionRight)
        rule.onNodeWithText("后退10秒").assertIsFocused()
        rule.runOnIdle { assertEquals(1, toggles) }
    }

    @Test fun releasingTheOkPressThatRevealedControlsDoesNotClickTheNewlyFocusedButton() {
        mount()
        back()
        rule.onNodeWithText("暂停").assertDoesNotExist()

        // Model an actual remote: the UI can recompose and focus the button before key-up arrives.
        rule.onRoot().performKeyInput { keyDown(Key.DirectionCenter) }
        rule.waitForIdle()
        rule.onNodeWithText("播放").assertIsFocused()
        rule.runOnIdle { assertEquals(1, toggles) }

        rule.onRoot().performKeyInput { keyUp(Key.DirectionCenter) }
        rule.waitForIdle()
        rule.onNodeWithText("播放").assertIsFocused()
        rule.runOnIdle { assertEquals(1, toggles) }
        press(Key.DirectionRight)
        rule.onNodeWithText("后退10秒").assertIsFocused()
    }

    @Test fun upAndDownRevealControlsWithoutTogglingAndLocalCacheUsesTheSameFocusPolicy() {
        mount(localCache = true)
        for (key in listOf(Key.DirectionUp, Key.DirectionDown)) {
            back()
            press(key)
            rule.onNodeWithText("暂停").assertIsFocused()
        }
        rule.runOnIdle { assertEquals(0, toggles) }
    }

    @Test fun leavingTheTimelineCancelsAnUncommittedSeek() {
        mount()
        press(Key.DirectionUp)
        rule.onNodeWithContentDescription("在线播放进度").assertIsFocused()
        press(Key.DirectionRight)
        timelineAt("0:40 / 10:00")

        press(Key.DirectionDown)
        // Like the local player, native spatial navigation picks an action based on geometry.
        rule.onNode(isFocused() and hasClickAction()).assertExists()
        timelineAt("0:30 / 10:00")
        press(Key.DirectionUp)
        rule.onNodeWithContentDescription("在线播放进度").assertIsFocused()
        timelineAt("0:30 / 10:00")
        rule.runOnIdle { assertTrue(seeks.isEmpty()) }
    }

    @Test fun backCancelsSeekBeforeHidingControlsAndClosingPlayback() {
        mount()
        press(Key.DirectionUp)
        press(Key.DirectionRight)
        timelineAt("0:40 / 10:00")

        back()
        rule.onNodeWithContentDescription("在线播放进度").assertIsFocused()
        timelineAt("0:30 / 10:00")
        rule.runOnIdle { assertEquals(0, closes); assertTrue(seeks.isEmpty()) }

        back()
        rule.onNodeWithContentDescription("在线播放进度").assertDoesNotExist()
        rule.runOnIdle { assertEquals(0, closes) }
        back()
        rule.runOnIdle { assertEquals(1, closes) }
    }

    @Test fun hiddenSeekCommitsOnlyOnOkAndDoesNotTogglePlayback() {
        mount()
        back()
        press(Key.DirectionRight)
        rule.onNodeWithText("跳转至 0:40 / 10:00").assertExists()
        press(Key.DirectionCenter)
        rule.onNodeWithText("跳转至 0:40 / 10:00").assertDoesNotExist()
        rule.onNodeWithText("暂停").assertDoesNotExist()
        rule.runOnIdle { assertEquals(listOf(40.0), seeks); assertEquals(0, toggles) }

        press(Key.DirectionLeft)
        back()
        rule.runOnIdle { assertEquals(listOf(40.0), seeks); assertEquals(0, closes) }
    }

    @Test fun mediaSkipKeysUseTheLocalPlayersThirtySecondStepWithoutRevealingControls() {
        mount()
        back()
        press(Key.MediaFastForward)
        press(Key.MediaRewind)
        rule.onNodeWithText("暂停").assertDoesNotExist()
        rule.runOnIdle { assertEquals(listOf(30.0, -30.0), skips) }
    }

    @Test fun settingSubmenuReturnsToSettingsAndHardwareBackReturnsToPlay() {
        mount()
        openSettings()
        press(Key.DirectionDown)
        rule.onNodeWithText("播放速度 · 1.0×").assertIsFocused()
        press(Key.DirectionCenter)
        rule.onNodeWithText("返回播放设置").assertIsFocused()
        press(Key.DirectionCenter)
        rule.onNodeWithText("返回播放").assertIsFocused()
        rule.onNodeWithText("播放速度 · 1.0×").assertExists()

        press(Key.DirectionDown)
        press(Key.DirectionCenter)
        back()
        rule.onNodeWithText("暂停").assertIsFocused()
        press(Key.DirectionRight)
        rule.onNodeWithText("后退10秒").assertIsFocused()
    }

    @Test fun completingNextEpisodeLookupDoesNotStealMenuFocus() {
        state.value = ready().copy(playing = false, playWhenReady = false, ended = true, nextLoading = true)
        mount()
        rule.onNodeWithText("重新播放").assertIsFocused()
        // The seek actions are disabled after the episode ends.
        press(Key.DirectionRight)
        rule.onNodeWithText("播放设置").assertIsFocused()
        press(Key.DirectionCenter)
        press(Key.DirectionDown)
        rule.onNodeWithText("播放速度 · 1.0×").assertIsFocused()

        rule.runOnIdle { state.value = state.value.copy(nextLoading = false) }
        rule.onNodeWithText("播放速度 · 1.0×").assertIsFocused()
    }

    @Test fun replayKeepsThePrimaryActionFocusedAndUsesRetryInsteadOfToggle() {
        mount()
        rule.runOnIdle { state.value = state.value.copy(ended = true, playing = false, playWhenReady = false) }
        rule.onNodeWithText("重新播放").assertIsFocused()

        press(Key.DirectionCenter)
        rule.onNodeWithText("暂停").assertIsFocused()
        press(Key.DirectionRight)
        rule.onNodeWithText("后退10秒").assertIsFocused()
        rule.runOnIdle { assertEquals(1, retries); assertEquals(0, toggles) }
    }

    @Test fun endedPlaybackCanHideControlsAndReplayOnceFromOkOrTheMediaKey() {
        state.value = ready().copy(ended = true, playing = false, playWhenReady = false)
        mount()
        rule.onNodeWithText("重新播放").assertIsFocused()
        back()
        rule.onNodeWithText("重新播放").assertDoesNotExist()
        rule.runOnIdle { assertEquals(0, closes) }

        rule.onRoot().performKeyInput { keyDown(Key.DirectionCenter) }
        rule.waitForIdle()
        rule.onNodeWithText("暂停").assertIsFocused()
        rule.onRoot().performKeyInput { keyUp(Key.DirectionCenter) }
        rule.waitForIdle()
        rule.onNodeWithText("暂停").assertIsFocused()
        rule.runOnIdle { assertEquals(1, retries); assertEquals(0, toggles) }

        rule.runOnIdle { state.value = state.value.copy(ended = true, playing = false, playWhenReady = false) }
        press(Key.MediaPlayPause)
        rule.onNodeWithText("暂停").assertIsFocused()
        rule.runOnIdle { assertEquals(2, retries); assertEquals(0, toggles); assertEquals(0, closes) }
    }

    @Test fun removingTheFocusedCancelNextActionRecoversToReplay() {
        state.value = ready().copy(playing = false, playWhenReady = false, ended = true, nextLoading = true)
        mount()
        press(Key.DirectionRight)
        rule.onNodeWithText("播放设置").assertIsFocused()
        press(Key.DirectionRight)
        rule.onNodeWithText("取消连续播放").assertIsFocused()

        press(Key.DirectionCenter)
        rule.onNodeWithText("取消连续播放").assertDoesNotExist()
        rule.onNodeWithText("重新播放").assertIsFocused()
        rule.runOnIdle { assertEquals(1, nextCancellations) }
    }

    @Test fun pausedControlsStayVisibleAndPlayingControlsHideOnlyAfterInactivity() {
        fun advanceTo(time: Long) {
            val remaining = time - rule.mainClock.currentTime
            if (remaining > 0) rule.mainClock.advanceTimeBy(remaining)
        }
        state.value = ready().copy(playing = false, playWhenReady = false)
        mount()
        rule.mainClock.advanceTimeBy(7000)
        rule.onNodeWithText("播放").assertIsFocused()

        // press() waits for button animations and can itself advance the clock. Measure from
        // each input's start, so settling those animations is included in the inactivity interval.
        val resumedAt = rule.mainClock.currentTime
        press(Key.DirectionCenter)
        advanceTo(resumedAt + 5000)
        rule.onNodeWithText("暂停").assertIsFocused()
        val interactedAt = rule.mainClock.currentTime
        press(Key.DirectionRight)
        advanceTo(interactedAt + 5000)
        rule.onNodeWithText("后退10秒").assertIsFocused()
        assertTrue("The second visible check must cross the original inactivity deadline",
            rule.mainClock.currentTime >= resumedAt + 6000)
        advanceTo(interactedAt + 6500)
        rule.onNodeWithText("暂停").assertDoesNotExist()

        press(Key.DirectionDown)
        rule.onNodeWithText("暂停").assertIsFocused()
    }

    @Test fun becomingSeekableAndBufferingDoNotStealTheCurrentActionFocus() {
        state.value = ready().copy(loading = true, firstFrame = false, duration = 0.0, seekable = false)
        mount()
        rule.onNodeWithText("暂停").assertIsFocused()
        rule.runOnIdle { state.value = ready() }
        rule.onNodeWithText("暂停").assertIsFocused()
        press(Key.DirectionRight)
        rule.onNodeWithText("后退10秒").assertIsFocused()
        rule.runOnIdle { state.value = state.value.copy(playing = false, loading = true) }
        rule.onNodeWithText("后退10秒").assertIsFocused()
    }

    @Test fun endingPlaybackRecoversFocusFromTheTimelineAndBothSkipActions() {
        mount()
        for (control in listOf("timeline", "rewind", "forward")) {
            focusSeekControl(control)
            rule.runOnIdle { state.value = state.value.copy(ended = true, playing = false, playWhenReady = false) }
            rule.onNodeWithText("重新播放").assertIsFocused()
            // Directions must work immediately, skipping all disabled seek actions.
            press(Key.DirectionRight)
            rule.onNodeWithText("播放设置").assertIsFocused()
            press(Key.DirectionLeft)
            rule.onNodeWithText("重新播放").assertIsFocused()
            rule.runOnIdle { state.value = ready() }
            rule.onNodeWithText("暂停").assertIsFocused()
        }
        rule.runOnIdle { assertTrue(seeks.isEmpty()); assertTrue(skips.isEmpty()) }
    }

    @Test fun temporarilyLosingSeekCapabilityRecoversFocusFromTheTimelineAndBothSkipActions() {
        mount()
        for (control in listOf("timeline", "rewind", "forward")) {
            focusSeekControl(control)
            rule.runOnIdle { state.value = state.value.copy(seekable = false, loading = true, playing = false) }
            rule.onNodeWithText("暂停").assertIsFocused()
            press(Key.DirectionRight)
            rule.onNodeWithText("播放设置").assertIsFocused()
            press(Key.DirectionLeft)
            rule.onNodeWithText("暂停").assertIsFocused()
            rule.runOnIdle { state.value = ready() }
            rule.onNodeWithText("暂停").assertIsFocused()
        }
        rule.runOnIdle { assertTrue(seeks.isEmpty()); assertTrue(skips.isEmpty()) }
    }

    private fun mount(localCache: Boolean = false) {
        val request = OnlinePlaybackRequest(
            OnlineIdentity("src_${"1".repeat(32)}", "itm_${"2".repeat(32)}", "movie"),
            "隔离焦点测试", "focus-fixture", scopeToken = "", localCache = localCache)
        val actions = OnlinePlayerActions(
            toggle = {
                toggles++
                val play = !state.value.playWhenReady
                state.value = state.value.copy(playing = play, playWhenReady = play)
            },
            setPlaying = { play -> state.value = state.value.copy(playing = play, playWhenReady = play) },
            skip = { skips += it },
            seek = { seeks += it; state.value = state.value.copy(position = it) },
            retry = { retries++; state.value = ready() },
            cancelNext = { nextCancellations++; state.value = state.value.copy(nextLoading = false) },
            playNetworkNext = {},
            setRate = { state.value = state.value.copy(rate = it) },
            setTrack = { _, _ -> },
            close = { closes++ },
        )
        rule.setContent { MaterialTheme { OnlinePlayerContent(request, state.value, actions) } }
        rule.waitForIdle()
    }

    private fun openSettings() {
        repeat(3) { press(Key.DirectionRight) }
        rule.onNodeWithText("播放设置").assertIsFocused()
        press(Key.DirectionCenter)
        rule.onNodeWithText("返回播放").assertIsFocused()
    }

    private fun focusSeekControl(control: String) {
        when (control) {
            "timeline" -> {
                press(Key.DirectionUp)
                rule.onNodeWithContentDescription("在线播放进度").assertIsFocused()
            }
            "rewind" -> {
                press(Key.DirectionRight)
                rule.onNodeWithText("后退10秒").assertIsFocused()
            }
            "forward" -> {
                repeat(2) { press(Key.DirectionRight) }
                rule.onNodeWithText("前进10秒").assertIsFocused()
            }
            else -> error("Unknown seek control: $control")
        }
    }

    private fun timelineAt(value: String) {
        rule.onNodeWithContentDescription("在线播放进度").assert(
            SemanticsMatcher.expectValue(SemanticsProperties.StateDescription, value))
    }

    private fun press(key: Key) {
        rule.onRoot().performKeyInput { pressKey(key) }
        rule.waitForIdle()
    }

    private fun back() {
        // The activity dispatcher is the system/remote Back entry used by BackHandler.
        rule.runOnIdle { rule.activity.onBackPressedDispatcher.onBackPressed() }
        rule.waitForIdle()
    }

    private fun ready() = OnlinePlaybackState(loading = false, playing = true, firstFrame = true,
        position = 30.0, duration = 600.0, buffered = 120.0, seekable = true, transport = "mp4")
}
