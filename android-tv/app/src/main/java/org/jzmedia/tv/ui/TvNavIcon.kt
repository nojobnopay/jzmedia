package org.jzmedia.tv.ui

import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier

/** Navigation aliases resolve in the generated registry; labels belong to the button. */
@Composable
internal fun TvNavIcon(kind: String, modifier: Modifier = Modifier) = TvIcon(kind, modifier)
