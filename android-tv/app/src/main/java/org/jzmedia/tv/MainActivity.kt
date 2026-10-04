package org.jzmedia.tv

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.CompositionLocalProvider
import org.jzmedia.tv.ui.generated.DesignTokens
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.darkColorScheme
import org.jzmedia.tv.ui.TvApp
import org.jzmedia.tv.ui.TvTypography

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(
                typography = TvTypography,
                colorScheme = darkColorScheme(
                    primary = DesignTokens.Accent,
                    onPrimary = DesignTokens.TextStrong,
                    background = DesignTokens.Background,
                    onBackground = DesignTokens.Text,
                    surface = DesignTokens.SurfaceRaised,
                    onSurface = DesignTokens.Text,
                ),
            ) {
                CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onBackground) {
                    TvApp(onExit = ::finish)
                }
            }
        }
    }
}
