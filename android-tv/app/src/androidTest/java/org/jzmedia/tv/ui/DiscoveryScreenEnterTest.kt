package org.jzmedia.tv.ui

import android.os.SystemClock
import android.view.InputDevice
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputConnection
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import androidx.tv.material3.MaterialTheme
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.ViewingSession
import java.util.concurrent.TimeUnit

class DiscoveryScreenEnterTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val query = "周星驰最有名的电影有哪些"
    private val hint = "想看什么电影或剧集？"
    private val openedDetails = mutableListOf<Long>()

    private fun openEditor(server: MockWebServer): InputConnection {
        // Use the private session's in-memory history and text input on every device.
        val api = JzApi(server.url("/").toString(), viewing = ViewingSession(mode = "private"), initialAiDiscovery = true)
        instrumentation.setInTouchMode(true)
        rule.runOnUiThread {
            // Mount on the Activity so the platform IME owns the real input connection.
            rule.activity.setContent {
                MaterialTheme {
                    var route by remember { mutableStateOf(TvRoute("search")) }
                    key(route.key) {
                        val memory = rememberFocusMemory(if (route.kind == "discovery") "discovery:input" else "key:A")
                        val enter = remember { DiscoveryEnterPress() }
                        val pageKeys = if (route.kind == "discovery") Modifier.onPreviewKeyEvent(enter::intercept) else Modifier
                        Column(pageKeys.fillMaxSize().padding(20.dp)) {
                            TvNavigation("search", "全部媒体库", memory) { route = TvRoute(it) }
                            if (route.kind == "discovery") {
                                AiDiscoveryScreen(api, 0, route.title, remember { AiDiscoveryScreenState() }, memory, enter,
                                    onDetail = { openedDetails.add(it.id) }, onOnlineSearch = { error("unexpected online search") },
                                    isCurrent = { route.kind == "discovery" })
                            } else SearchScreen(api, 0, "all", memory, { route = it }, {})
                        }
                    }
                }
            }
        }
        rule.onNodeWithText("智能找片").performSemanticsAction(SemanticsActions.OnClick)
        val field = rule.onNodeWithContentDescription(hint)
        field.performTouchInput { click() }
        field.assertIsFocused()
        val connection = rule.runOnIdle {
            checkNotNull(rule.activity.currentFocus?.onCreateInputConnection(EditorInfo())) {
                "智能找片应提供 Android 输入连接"
            }.also { assertTrue(it.commitText(query, 1)) }
        }
        rule.onNodeWithText(query).assertIsDisplayed()
        return connection
    }

    private fun verifyDiscovery(server: MockWebServer) {
        // Platform IME callbacks arrive asynchronously. Run Compose frames before blocking on the socket.
        rule.waitUntil(8_000) { rule.onAllNodesWithText("正在找片…").fetchSemanticsNodes().isNotEmpty() }
        rule.onNodeWithText("正在找片…").assertIsDisplayed()
        val request = server.takeRequest(8, TimeUnit.SECONDS)
        assertNotNull("智能找片没有收到搜索请求", request)
        assertEquals("POST", request!!.method)
        assertEquals("/api/ai/search/results", request.path)
        assertEquals(query, JSONObject(request.body.readUtf8()).getString("q"))
        server.enqueue(MockResponse().setBody("""{"ok":true,"source":"library","session_id":"12345678901234567890123456789012",
            "summary":"找到一部测试影片","items":[{"key":"movie:1","id":1,"in_library":true,"kind":"movie","title":"合成测试片"}]}"""))
        rule.waitUntil(8_000) { rule.onAllNodesWithText("1. 合成测试片").fetchSemanticsNodes().isNotEmpty() }
        rule.onNodeWithText("1. 合成测试片").assertIsDisplayed()
        rule.onNodeWithText("输入片名首字母").assertDoesNotExist()
        assertEquals(1, server.requestCount)
    }

    @Test fun touchThenKeyboardEnterShowsSmartResults() {
        MockWebServer().use { server ->
            openEditor(server)
            instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_ENTER)
            verifyDiscovery(server)
        }
    }

    @Test fun imeSearchFollowedByEnterReleaseStaysOnSmartResults() {
        MockWebServer().use { server ->
            val editor = openEditor(server)
            val downTime = SystemClock.uptimeMillis()
            rule.runOnIdle { assertTrue(editor.performEditorAction(EditorInfo.IME_ACTION_SEARCH)) }
            rule.onNodeWithText("正在找片…").assertIsDisplayed()
            // Some IMEs translate DOWN to Search, but pass the corresponding UP to the window.
            instrumentation.sendKeySync(KeyEvent(downTime, SystemClock.uptimeMillis(), KeyEvent.ACTION_UP,
                KeyEvent.KEYCODE_ENTER, 0, 0, KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD))
            verifyDiscovery(server)
        }
    }

    @Test fun editorDownThenImeSearchAndLateReleaseDoNotActivateFirstResult() {
        MockWebServer().use { server ->
            val editor = openEditor(server)
            val downTime = SystemClock.uptimeMillis()
            fun deliverToWindow(action: Int) = rule.runOnIdle {
                rule.activity.dispatchKeyEvent(KeyEvent(downTime, SystemClock.uptimeMillis(), action,
                    KeyEvent.KEYCODE_ENTER, 0, 0, KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD))
            }
            // Some keyboards deliver DOWN to the Activity, then request Search via the IME before UP.
            deliverToWindow(KeyEvent.ACTION_DOWN)
            rule.runOnIdle { assertTrue(editor.performEditorAction(EditorInfo.IME_ACTION_SEARCH)) }
            verifyDiscovery(server)
            val card = rule.onNodeWithText("1. 合成测试片")
            card.assertIsFocused()
            deliverToWindow(KeyEvent.ACTION_UP)
            card.assertIsFocused()
            rule.runOnIdle { assertTrue(openedDetails.isEmpty()) }
            instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_ENTER)
            rule.runOnIdle { assertEquals(listOf(1L), openedDetails) }
            assertEquals(1, server.requestCount)
        }
    }
}
