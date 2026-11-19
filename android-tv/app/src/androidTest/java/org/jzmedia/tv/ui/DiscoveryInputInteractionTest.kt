package org.jzmedia.tv.ui

import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyCharacterMap
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.SoftwareKeyboardController
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performImeAction
import androidx.tv.material3.MaterialTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class DiscoveryInputInteractionTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private val hint = "想看什么电影或剧集？"
    private val draft = mutableStateOf("")
    private val busy = mutableStateOf(false)
    private val submissions = mutableListOf<String>()
    private val navigation = mutableListOf<String>()
    private var keyboardShows = 0

    private fun mount(query: String) {
        draft.value = query
        val field = FocusRequester()
        val nav = FocusRequester()
        val memory = FocusMemory("discovery:input")
        val keyboard = object : SoftwareKeyboardController {
            override fun show() { keyboardShows++ }
            override fun hide() {}
        }
        rule.setContent {
            MaterialTheme {
                CompositionLocalProvider(LocalSoftwareKeyboardController provides keyboard) {
                    Column {
                        Box(Modifier.focusRequester(nav)) {
                            TvNavigation("search", "全部媒体库", memory) { navigation.add(it) }
                        }
                        DiscoveryInput(draft.value, { draft.value = it }, hint, Modifier.focusRequester(field),
                            enabled = !busy.value) {
                            submissions.add(draft.value)
                            busy.value = true
                        }
                    }
                    LaunchedEffect(busy.value) {
                        if (busy.value) nav.requestFocus() else field.requestFocus()
                    }
                }
            }
        }
        rule.onNodeWithContentDescription(hint).assertIsFocused()
    }

    private fun dispatch(code: Int, action: Int, downTime: Long, repeats: Int = 0) {
        val source = if (code == KeyEvent.KEYCODE_DPAD_CENTER) InputDevice.SOURCE_DPAD else InputDevice.SOURCE_KEYBOARD
        rule.runOnIdle {
            rule.activity.dispatchKeyEvent(KeyEvent(downTime, SystemClock.uptimeMillis(), action, code, repeats,
                0, KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, source))
        }
        rule.waitForIdle()
    }

    private fun checkEnter(code: Int) {
        mount("周星驰最有名的电影")
        val downTime = SystemClock.uptimeMillis()
        dispatch(code, KeyEvent.ACTION_DOWN, downTime)
        // An input method can commit its last characters while the physical key is held.
        rule.runOnIdle { draft.value += "有哪些" }
        dispatch(code, KeyEvent.ACTION_DOWN, downTime, repeats = 1)
        val submittedBeforeRelease = rule.runOnIdle { submissions.isNotEmpty() }
        dispatch(code, KeyEvent.ACTION_UP, downTime)
        rule.runOnIdle {
            assertEquals("普通导航不应收到这次回车", emptyList<String>(), navigation)
            assertTrue("松开回车前应能继续输入", !submittedBeforeRelease)
            assertEquals(listOf("周星驰最有名的电影有哪些"), submissions)
        }
        rule.onNodeWithText("搜索").assertIsFocused()
    }

    @Test fun enterSubmitsOneDiscoveryQueryWithoutOpeningOrdinarySearch() = checkEnter(KeyEvent.KEYCODE_ENTER)

    @Test fun numpadEnterSubmitsOneDiscoveryQueryWithoutOpeningOrdinarySearch() = checkEnter(KeyEvent.KEYCODE_NUMPAD_ENTER)

    @Test fun emptyEnterAndRemoteCenterOpenTheEditor() {
        mount("")
        for (code in listOf(KeyEvent.KEYCODE_ENTER, KeyEvent.KEYCODE_DPAD_CENTER)) {
            val before = rule.runOnIdle { keyboardShows }
            val downTime = SystemClock.uptimeMillis()
            dispatch(code, KeyEvent.ACTION_DOWN, downTime)
            dispatch(code, KeyEvent.ACTION_UP, downTime)
            rule.onNodeWithContentDescription(hint).assertIsFocused()
            rule.runOnIdle {
                assertTrue(keyboardShows > before)
                assertTrue(submissions.isEmpty())
                assertTrue(navigation.isEmpty())
                draft.value = "继续编辑这句话"
            }
        }
    }

    @Test fun standaloneKeyReleaseDoesNotSubmitAndImeSearchStillWorks() {
        mount("推荐一部喜剧")
        dispatch(KeyEvent.KEYCODE_ENTER, KeyEvent.ACTION_UP, SystemClock.uptimeMillis())
        rule.runOnIdle { assertTrue(submissions.isEmpty()) }
        rule.onNodeWithContentDescription(hint).performImeAction()
        rule.runOnIdle {
            assertEquals(listOf("推荐一部喜剧"), submissions)
            assertTrue(navigation.isEmpty())
        }
    }
}
