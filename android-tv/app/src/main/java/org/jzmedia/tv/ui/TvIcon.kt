package org.jzmedia.tv.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.tv.material3.Icon
import org.jzmedia.tv.ui.generated.DesignIcons
import org.jzmedia.tv.ui.generated.DesignTokens

/** The adjacent label owns the accessible name. All paths come from design/. */
@Composable
fun TvIcon(name: String, modifier: Modifier = Modifier) {
    val rotation = if (name == "loading") {
        val transition = rememberInfiniteTransition(label = "loading")
        val angle by transition.animateFloat(0f, 360f,
            infiniteRepeatable(tween(1000, easing = LinearEasing), RepeatMode.Restart), label = "rotation")
        angle
    } else 0f
    Icon(DesignIcons.get(name), contentDescription = null,
        modifier = modifier.size(DesignTokens.IconSize).rotate(rotation))
}
