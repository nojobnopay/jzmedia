package org.jzmedia.tv.ui

import org.jzmedia.tv.ui.generated.DesignIcons
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

/** Navigation and playback kept different meanings for the old `forward` name. */
class DesignIconsTest {
    @Test fun navigationAliasesKeepTheSameCrossPlatformAsset() {
        assertEquals(DesignIcons.get("movie"), DesignIcons.get("movies"))
        assertEquals(DesignIcons.get("tv"), DesignIcons.get("shows"))
        assertEquals(DesignIcons.get("library"), DesignIcons.get("libraries"))
    }

    @Test fun forwardNavigationDoesNotBecomeSeekTenSeconds() {
        assertNotEquals(DesignIcons.get("forward"), DesignIcons.get("forward10"))
        assertEquals(DesignIcons.get("skip-forward-10"), DesignIcons.get("forward10"))
        assertEquals(DesignIcons.get("skip-back-10"), DesignIcons.get("rewind"))
    }

    @Test fun allTvControlVectorsRetainTheSharedGrid() {
        for (name in listOf("search", "home", "movies", "shows", "collections", "libraries", "settings",
            "play", "pause", "replay", "rewind", "forward10", "next", "previous", "back", "close",
            "filter", "refresh", "volume", "subtitles", "speed", "quality", "more", "edit", "logout",
            "link", "trash", "person", "image", "info", "error", "loading")) {
            val vector = DesignIcons.get(name)
            assertEquals(name, 24f, vector.viewportWidth, 0f)
            assertEquals(name, 24f, vector.viewportHeight, 0f)
        }
    }
}
