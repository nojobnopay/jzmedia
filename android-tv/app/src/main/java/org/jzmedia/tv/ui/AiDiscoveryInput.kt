package org.jzmedia.tv.ui

import android.content.Intent
import android.speech.RecognizerIntent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import org.jzmedia.tv.ui.generated.DesignTokens

internal fun discoverySpeechIntent() = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
    putExtra(RecognizerIntent.EXTRA_PROMPT, "说说你想看的电影或剧集")
    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
}

/** An IME can submit between the DOWN and UP delivered to the Activity for the same key. */
internal class DiscoveryEnterPress {
    private var down: Key? = null

    fun submitted() { down = null }

    // The page includes the navigation bar and the results that may gain focus after submission.
    fun intercept(event: KeyEvent): Boolean {
        if (event.key != Key.Enter && event.key != Key.NumPadEnter) return false
        if (event.type == KeyEventType.KeyDown) {
            if (event.nativeKeyEvent.repeatCount == 0) down = event.key
            return down != event.key
        }
        if (event.type != KeyEventType.KeyUp) return false
        val handled = down != event.key || event.nativeKeyEvent.isCanceled
        down = null
        return handled
    }
}

@Composable
internal fun DiscoveryInput(value: String, onValue: (String) -> Unit, hint: String,
                            modifier: Modifier, enabled: Boolean, readOnly: Boolean = false, submit: () -> Unit) {
    var focused by remember { mutableStateOf(false) }
    var pressedKey by remember { mutableStateOf<Key?>(null) }
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = LocalFocusManager.current
    val shape = RoundedCornerShape(DesignTokens.CornerRadius)
    val style = MaterialTheme.typography.bodyLarge
    BasicTextField(value, onValue, singleLine = true, enabled = enabled, readOnly = readOnly,
        textStyle = style.copy(color = DesignTokens.TextStrong), cursorBrush = SolidColor(DesignTokens.TextStrong),
        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
        keyboardActions = KeyboardActions(onSearch = { if (!readOnly) submit() }, onDone = { if (!readOnly) submit() }),
        modifier = modifier.fillMaxWidth().heightIn(min = DesignTokens.ControlHeight)
            .semantics { contentDescription = hint }.onFocusChanged {
                focused = it.isFocused
                if (!it.isFocused) pressedKey = null
            }.onPreviewKeyEvent {
                when {
                    it.key == Key.Enter || it.key == Key.NumPadEnter || it.key == Key.DirectionCenter -> {
                        if (it.type == KeyEventType.KeyDown && it.nativeKeyEvent.repeatCount == 0 && enabled && !readOnly) {
                            pressedKey = it.key
                        } else if (it.type == KeyEventType.KeyUp && pressedKey == it.key) {
                            pressedKey = null
                            if (enabled && !readOnly && !it.nativeKeyEvent.isCanceled) {
                                if (it.key != Key.DirectionCenter && value.isNotBlank()) submit()
                                else keyboard?.show()
                            }
                        }
                        true
                    }
                    it.type == KeyEventType.KeyDown && it.key == Key.DirectionUp -> focus.moveFocus(FocusDirection.Up)
                    it.type == KeyEventType.KeyDown && it.key == Key.DirectionDown -> focus.moveFocus(FocusDirection.Down)
                    it.type == KeyEventType.KeyDown && it.key == Key.DirectionRight && value.isBlank() ->
                        focus.moveFocus(FocusDirection.Right)
                    else -> false
                }
            }.border(if (focused) 3.dp else 1.dp, if (focused) DesignTokens.TextStrong else DesignTokens.Border, shape)
            .background(Panel, shape).padding(12.dp),
        decorationBox = { field -> Box {
            if (value.isBlank()) Text(hint, style = style, color = Muted)
            field()
        } },
    )
}
