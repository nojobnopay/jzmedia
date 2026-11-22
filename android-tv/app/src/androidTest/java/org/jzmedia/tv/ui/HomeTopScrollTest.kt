package org.jzmedia.tv.ui

import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import androidx.tv.material3.MaterialTheme
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.jzmedia.tv.data.JzApi

class HomeTopScrollTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val hero = "横幅测试片"

    private fun homeData() = object : Dispatcher() {
        override fun dispatch(request: RecordedRequest): MockResponse {
            val items = JSONArray()
            when (request.requestUrl?.encodedPath) {
                "/api/movies/recent-played" -> for (i in 1..4) {
                    items.put(JSONObject().put("id", i).put("title", if (i == 1) hero else "续播$i")
                        .put("progress", JSONObject().put("version_id", i).put("last_played_at", 100 - i)
                            .put("position", 300).put("duration", 7200)))
                }
                "/api/movies", "/api/tv/shows" -> for (i in 1..8) {
                    val kind = if (request.requestUrl?.encodedPath == "/api/movies") "电影" else "剧集"
                    items.put(JSONObject().put("id", i).put("title", "$kind$i"))
                }
            }
            return MockResponse().setBody(JSONObject().put("items", items).toString())
        }
    }

    private fun press(code: Int) {
        instrumentation.sendKeyDownUpSync(code)
        rule.waitForIdle()
    }

    private fun moveTo(memory: FocusMemory, prefix: String, key: Int, onFocus: (String) -> Unit = {}) {
        repeat(10) {
            val current = rule.runOnIdle { memory.key }
            onFocus(current)
            if (current.startsWith(prefix)) return
            press(key)
        }
        fail("Expected $prefix, reached ${rule.runOnIdle { memory.key }}")
    }

    private fun top(text: String) =
        rule.onNodeWithText(text, useUnmergedTree = true).fetchSemanticsNode().boundsInRoot.top

    private fun scrollPosition(): Float {
        val axis = SemanticsProperties.VerticalScrollAxisRange
        val range = rule.onNode(SemanticsMatcher.keyIsDefined(axis)).fetchSemanticsNode().config[axis]
        return rule.runOnIdle { range.value() }
    }

    @Test fun returningFromTheShowRailToNavigationRestoresTheWholeHero() {
        MockWebServer().use { server ->
            server.dispatcher = homeData()
            val api = JzApi(server.url("/").toString(), initialAiDiscovery = true)
            val memory = FocusMemory("nav:home")
            instrumentation.setInTouchMode(false)
            rule.setContent {
                MaterialTheme {
                    var navigationFocused by remember { mutableStateOf(false) }
                    var discovery by remember { mutableStateOf<FocusRequester?>(null) }
                    Column(Modifier.heightIn(max = 560.dp).fillMaxSize().padding(horizontal = 40.dp, vertical = 20.dp),
                        verticalArrangement = Arrangement.spacedBy(16.dp)) {
                        TvNavigation("home", "全部媒体库", memory, homeDown = discovery,
                            modifier = Modifier.onFocusChanged { navigationFocused = it.hasFocus }) {}
                        HomeScreen(api, 0, 0, memory, {}, {}, navigationFocused) { discovery = it }
                    }
                }
            }
            rule.waitUntil(8_000) { rule.onAllNodesWithText(hero).fetchSemanticsNodes().isNotEmpty() }
            rule.onNodeWithText("首页").assertIsFocused()
            val titleTop = top(hero)
            val refreshTop = top("刷新")
            assertEquals(0f, scrollPosition(), 0f)
            fun assertHomeAtTop() {
                assertEquals(0f, scrollPosition(), 0f)
                assertEquals(titleTop, top(hero), 1f)
                assertEquals(refreshTop, top("刷新"), 1f)
            }

            moveTo(memory, "newshow:", KeyEvent.KEYCODE_DPAD_DOWN)
            assertTrue("The show rail must scroll the home list", scrollPosition() > 0f)
            var reachedBannerAction = false
            moveTo(memory, "nav:", KeyEvent.KEYCODE_DPAD_UP) { current ->
                if (current in setOf("continue:movie:1", "home-discovery", "home-refresh")) {
                    reachedBannerAction = true
                    assertEquals("Banner action $current must reveal its whole banner", 0f, scrollPosition(), 0f)
                    assertEquals(titleTop, top(hero), 1f)
                }
            }
            assertTrue("Up navigation should pass through a banner action", reachedBannerAction)
            assertHomeAtTop()

            // Walk between channels without activating them, as when hovering over 电视剧 on the home page.
            moveTo(memory, "nav:search", KeyEvent.KEYCODE_DPAD_LEFT)
            moveTo(memory, "nav:shows", KeyEvent.KEYCODE_DPAD_RIGHT)
            rule.onNodeWithText("电视剧").assertIsFocused()
            assertHomeAtTop()
        }
    }
}
