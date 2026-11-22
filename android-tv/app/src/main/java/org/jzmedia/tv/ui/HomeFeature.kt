package org.jzmedia.tv.ui

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.BiasAlignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.tv.material3.LocalContentColor
import androidx.tv.material3.MaterialTheme
import androidx.tv.material3.Text
import org.json.JSONObject
import org.jzmedia.tv.data.JzApi
import org.jzmedia.tv.data.detailBackdropPath
import org.jzmedia.tv.data.mediaLibraryLabel
import org.jzmedia.tv.data.mediaRatingLabel
import org.jzmedia.tv.data.mediaTitle
import org.jzmedia.tv.data.posterPath
import org.jzmedia.tv.data.text
import org.jzmedia.tv.playback.playbackTime
import org.jzmedia.tv.playback.resumePosition
import org.jzmedia.tv.ui.generated.DesignTokens

internal data class HomeFeature(val row: JSONObject, val kind: String, val targetId: Long, val resume: Boolean) {
    val focusKey = if (resume) "continue:$kind:$targetId" else if (kind == "movie") "newmovie:$targetId" else "newshow:$targetId"
    val position = row.optJSONObject("progress")?.optDouble("position", 0.0) ?: 0.0
    val title = mediaTitle(row)
    val backdrop = detailBackdropPath(if (kind == "episode") "show" else kind, row, row.optLong("id"))
    val poster = posterPath(row)

    val metadata: String get() {
        val duration = row.optJSONObject("progress")?.optDouble("duration", 0.0) ?: 0.0
        return listOf(
            row.text("year").takeUnless { it == "0" }.orEmpty(),
            row.text("subtitle").takeIf { kind == "episode" }.orEmpty(),
            if (resume && duration.isFinite() && duration > position) "剩余 ${playbackTime(duration - position)}"
            else if (!resume) mediaRatingLabel(row) else "",
        ).filter(String::isNotBlank).joinToString("  ·  ")
    }
}

internal fun homeCanResume(row: JSONObject): Boolean {
    val progress = row.optJSONObject("progress") ?: return false
    val position = resumePosition(progress.optDouble("position", 0.0), progress.optDouble("duration", 0.0))
    return progress.optLong("version_id") > 0 && position > 0 &&
        row.optBoolean("can_resume", true) && !row.optBoolean("watched") && row.optInt("watched") != 1 &&
        !row.optBoolean("missing") && !knownOffline(row)
}

internal fun chooseHomeFeature(continuing: List<JSONObject>, movies: List<JSONObject>, shows: List<JSONObject>): HomeFeature? {
    val latest = continuing.firstOrNull()
    if (latest != null) return HomeFeature(latest, latest.text("kind", "movie"), latest.getJSONObject("progress").getLong("version_id"), true)
    for ((kind, rows) in listOf("movie" to movies, "show" to shows)) {
        val row = rows.firstOrNull { it.optLong("id") > 0 && !it.optBoolean("missing") && !knownOffline(it) }
        if (row != null) return HomeFeature(row, kind, row.getLong("id"), false)
    }
    return null
}

@Composable
internal fun HomeHeader(api: JzApi, feature: HomeFeature?, actionModifier: Modifier, onOpen: () -> Unit, modifier: Modifier = Modifier,
                        tools: @Composable () -> Unit) {
    val shape = RoundedCornerShape(DesignTokens.CardRadius)
    Box(modifier.fillMaxWidth().then(if (feature == null) Modifier else Modifier.clip(shape).background(DesignTokens.Surface))) {
        if (feature != null) HomeFeatureArtwork(api, feature, Modifier.matchParentSize())
        Column(Modifier.fillMaxWidth().then(if (feature == null) Modifier else Modifier.heightIn(min = 204.dp)
            .padding(horizontal = 22.dp, vertical = 12.dp)), verticalArrangement = Arrangement.SpaceBetween) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                if (feature == null) Text("今晚看什么", modifier = Modifier.weight(1f), style = MaterialTheme.typography.headlineMedium)
                else Spacer(Modifier.weight(1f))
                tools()
            }
            if (feature != null) {
                Column(Modifier.fillMaxWidth(.75f).padding(top = 6.dp, bottom = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(feature.title, style = MaterialTheme.typography.displaySmall, color = DesignTokens.TextStrong,
                        fontWeight = FontWeight.Bold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    if (feature.metadata.isNotBlank()) Text(feature.metadata, style = MaterialTheme.typography.bodyMedium,
                        color = DesignTokens.Text, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    TvAction(if (feature.resume) "从 ${playbackTime(feature.position)} 继续" else "查看详情",
                        onOpen, actionModifier.padding(top = 5.dp),
                        icon = if (feature.resume) "play" else "forward", primary = true)
                }
            }
        }
    }
}

@Composable
private fun HomeFeatureArtwork(api: JzApi, feature: HomeFeature, modifier: Modifier) {
    val backdrop = feature.backdrop
    val poster = feature.poster
    val artwork by key(api, backdrop, poster) {
        produceState<Pair<Bitmap, Boolean>?>(null) {
            value = loadPosterWithFallback(backdrop, poster, onFailure = { _, _ -> }) { path ->
                Posters.load(api, path, if (path == backdrop) PosterSize.BACKDROP else PosterSize.CARD)?.let { it to (path == backdrop) }
            }
        }
    }
    Box(modifier) {
        artwork?.let { (bitmap, wide) ->
            Image(bitmap.asImageBitmap(), contentDescription = null,
                modifier = if (wide) Modifier.fillMaxSize() else Modifier.align(Alignment.CenterEnd).fillMaxWidth(.48f).fillMaxHeight(),
                alignment = if (wide) BiasAlignment(0f, -.5f) else Alignment.CenterEnd,
                contentScale = if (wide) ContentScale.Crop else ContentScale.Fit)
        }
        Box(Modifier.matchParentSize().background(Brush.horizontalGradient(colorStops = arrayOf(
            0f to DesignTokens.Background.copy(alpha = .94f), .38f to DesignTokens.Background.copy(alpha = .74f),
            .7f to DesignTokens.Background.copy(alpha = .20f), 1f to Color.Transparent))))
        Box(Modifier.matchParentSize().background(Brush.verticalGradient(colorStops = arrayOf(
            0f to Color.Transparent, .6f to Color.Transparent, 1f to DesignTokens.Background.copy(alpha = .72f)))))
    }
}

@Composable
internal fun HomeContinueCard(api: JzApi, row: JSONObject, onClick: () -> Unit, modifier: Modifier) {
    val title = mediaTitle(row)
    val context = listOf(row.text("subtitle"), mediaLibraryLabel(row)).filter(String::isNotBlank).joinToString("  ·  ")
    val progress = row.getJSONObject("progress")
    val position = progress.optDouble("position", 0.0)
    val duration = progress.optDouble("duration", 0.0).takeIf { it.isFinite() && it > 0 }
    val remaining = if (duration == null) "已看 ${playbackTime(position)}"
        else "剩余 ${playbackTime((duration - position).coerceAtLeast(0.0))}"
    PosterButton(onClick, modifier.width(292.dp), 5.dp, DesignTokens.Surface) { captionModifier ->
        Row(Modifier.fillMaxWidth().padding(end = 9.dp), verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Poster(api, posterPath(row), title, Modifier.size(58.dp, 87.dp), showPlaceholderLabel = false)
            Column(Modifier.weight(1f).then(captionModifier), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (context.isNotBlank()) Text(context, color = LocalContentColor.current.copy(alpha = .76f),
                    style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(remaining, style = MaterialTheme.typography.labelMedium)
                if (duration != null) Box(Modifier.fillMaxWidth().height(4.dp)) {
                    PosterProgress((position / duration).toFloat())
                }
            }
        }
    }
}
