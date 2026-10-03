package org.jzmedia.tv

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.darkColorScheme
import org.jzmedia.tv.ui.TvApp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = Color(0xFFE50914),
                    onPrimary = Color.White,
                    background = Color(0xFF141414),
                    onBackground = Color(0xFFEEEEEE),
                    surface = Color(0xFF262626),
                    onSurface = Color(0xFFEEEEEE),
                ),
            ) {
                CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onBackground) {
                    TvApp(onExit = ::finish)
                }
            }
        }
    }
}
