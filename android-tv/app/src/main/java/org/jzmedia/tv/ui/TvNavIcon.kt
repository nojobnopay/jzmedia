package org.jzmedia.tv.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.unit.dp
import androidx.tv.material3.Icon

// Shared 24-unit paths with the web AppIcon: same silhouette, stroke and rounded ends.
private val NavigationIconPaths = mapOf(
    "search" to listOf("M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0", "m15 15 6 6"),
    "home" to listOf("M3 10.5 12 3l9 7.5", "M5 9v12h5v-7h4v7h5V9"),
    "movies" to listOf("M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z", "M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4M7 12h10"),
    "shows" to listOf("M4 7h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z", "m8 3 4 4 4-4"),
    "collections" to listOf("M7 3h10M5 6h14", "M5 9h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z"),
    "libraries" to listOf("M3 4h4v17H3ZM10 4h4v17h-4Z", "m16 5 4-1 3 16-4 1Z"),
    "settings" to listOf("m9 3-.6 2.4-1.5.9-2.4-.7-2.1 3.6 1.8 1.7v1.8l-1.8 1.7 2.1 3.6 2.4-.7 1.5.9L9 21h6l.6-2.4 1.5-.9 2.4.7 2.1-3.6-1.8-1.7v-1.8l1.8-1.7-2.1-3.6-2.4.7-1.5-.9L15 3Z", "M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0"),
)

private val NavigationIcons: Map<String, ImageVector> by lazy {
    NavigationIconPaths.mapValues { (name, paths) ->
        ImageVector.Builder(name = "navigation-$name", defaultWidth = 24.dp, defaultHeight = 24.dp,
            viewportWidth = 24f, viewportHeight = 24f).apply {
            paths.forEach { path ->
                addPath(pathData = PathParser().parsePathString(path).toNodes(), fill = null,
                    stroke = SolidColor(Color.White), strokeLineWidth = 1.7f,
                    strokeLineCap = StrokeCap.Round, strokeLineJoin = StrokeJoin.Round)
            }
        }.build()
    }
}

@Composable
internal fun TvNavIcon(kind: String, modifier: Modifier = Modifier) {
    // The adjacent text owns the accessible label; the icon inherits the button color.
    Icon(NavigationIcons.getValue(kind), contentDescription = null, modifier = modifier)
}
