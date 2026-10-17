package org.jzmedia.tv.playback

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class SeekPreviewsTest {
    @Test fun spriteCellsFollowSourceTimeAndDoNotInventUnfinishedPages() {
        val value = JSONObject("""{"interval":10,"count":60,"width":160,"height":90,"columns":5,"rows":5,
            "pages":["/api/stream/previews/cache/abc/page_0000.jpg"]}""")
        val manifest = SeekPreviewManifest.fromJson(value)!!
        assertEquals(SeekPreviewFrame(value.getJSONArray("pages").getString(0), 0, 0, 160, 90), manifest.frameAt(0.0))
        assertEquals(SeekPreviewFrame(value.getJSONArray("pages").getString(0), 640, 0, 160, 90), manifest.frameAt(49.9))
        assertEquals(SeekPreviewFrame(value.getJSONArray("pages").getString(0), 0, 90, 160, 90), manifest.frameAt(50.0))
        assertNull(manifest.frameAt(250.0))
        assertNull(manifest.frameAt(Double.NaN))
    }

    @Test fun malformedOrMissingManifestCannotSelectAnImage() {
        assertNull(SeekPreviewManifest.fromJson(JSONObject("""{"state":"missing","pages":[]}""")))
        assertNull(SeekPreviewManifest.fromJson(JSONObject("""{"interval":0,"count":1,"width":160,
            "height":90,"columns":5,"rows":5,"pages":[]}""")))
        assertNull(SeekPreviewManifest.fromJson(JSONObject("""{"interval":10,"count":1,"width":160,
            "height":90,"columns":5,"rows":5,"pages":["https://other.test/image.jpg"]}""")))
    }

    @Test fun highResolutionSheetsUseManifestPixelsAcrossRowsAndPages() {
        val first = "/api/stream/previews/cache/high/page_0000.jpg"
        val second = "/api/stream/previews/cache/high/page_0001.jpg"
        val manifest = SeekPreviewManifest.fromJson(JSONObject("""{"quality_revision":3,"interval":10,
            "count":60,"width":480,"height":270,"columns":5,"rows":5,
            "pages":["$first","$second"]}"""))!!

        assertEquals(SeekPreviewFrame(first, 1920, 0, 480, 270), manifest.frameAt(49.9))
        assertEquals(SeekPreviewFrame(first, 0, 270, 480, 270), manifest.frameAt(50.0))
        assertEquals(SeekPreviewFrame(first, 1920, 1080, 480, 270), manifest.frameAt(249.9))
        assertEquals(SeekPreviewFrame(second, 0, 0, 480, 270), manifest.frameAt(250.0))
        assertNull(manifest.frameAt(500.0))
    }
}
