package org.jzmedia.tv.ui

import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyCharacterMap
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import androidx.tv.material3.MaterialTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class PosterFocusInteractionTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private fun key(code: Int, action: Int, downTime: Long, repeatCount: Int = 0) {
        val source = if (code == KeyEvent.KEYCODE_ENTER) InputDevice.SOURCE_KEYBOARD else InputDevice.SOURCE_DPAD
        rule.runOnIdle {
            rule.activity.dispatchKeyEvent(KeyEvent(downTime, SystemClock.uptimeMillis(), action, code, repeatCount,
                0, KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, source))
        }
    }

    @Test fun arrowsMoveFocusAndHeldCenterOrEnterOpensOnlyTheFocusedPosterOnceOnRelease() {
        val opened = mutableListOf<String>()
        val first = FocusRequester()
        InstrumentationRegistry.getInstrumentation().setInTouchMode(false)
        rule.setContent {
            MaterialTheme {
                Row {
                    PosterWallCard("远山来信", "", { opened.add("远山来信") },
                        Modifier.width(120.dp).focusRequester(first)) {}
                    PosterWallCard("群岛之间", "", { opened.add("群岛之间") }, Modifier.width(120.dp)) {}
                }
                LaunchedEffect(Unit) { first.requestFocus() }
            }
        }
        val button = SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Button)
        fun poster(title: String) = rule.onNode(hasText(title) and button)
        poster("远山来信").assertHasClickAction().assertIsFocused()

        fun moveAndOpen(direction: Int, confirm: Int, title: String, previouslyOpened: List<String>) {
            val move = SystemClock.uptimeMillis()
            key(direction, KeyEvent.ACTION_DOWN, move)
            key(direction, KeyEvent.ACTION_UP, move)
            poster(title).assertHasClickAction().assertIsFocused()
            rule.runOnIdle { assertEquals(previouslyOpened, opened) }

            val press = SystemClock.uptimeMillis()
            key(confirm, KeyEvent.ACTION_DOWN, press)
            key(confirm, KeyEvent.ACTION_DOWN, press, repeatCount = 1)
            key(confirm, KeyEvent.ACTION_DOWN, press, repeatCount = 2)
            rule.runOnIdle { assertEquals(previouslyOpened, opened) }
            key(confirm, KeyEvent.ACTION_UP, press)
            rule.runOnIdle { assertEquals(previouslyOpened + title, opened) }
        }
        moveAndOpen(KeyEvent.KEYCODE_DPAD_RIGHT, KeyEvent.KEYCODE_DPAD_CENTER, "群岛之间", emptyList())
        moveAndOpen(KeyEvent.KEYCODE_DPAD_LEFT, KeyEvent.KEYCODE_ENTER, "远山来信", listOf("群岛之间"))
    }
}
