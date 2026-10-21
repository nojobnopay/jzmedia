package org.jzmedia.tv.playback

import org.json.JSONObject
import org.json.JSONArray
import org.junit.Assert.*
import org.junit.Test

class SeekPreviewsTest {
    private fun tailManifest(): JSONObject = JSONObject("""{
        "state":"ready","completion":"known_tail_missing","interval":10,"count":945,
        "duration":9449.147,"available_count":925,"coverage_end":9250,
        "width":480,"height":270,"columns":5,"rows":5,
        "warning":{"code":"confirmed_tail_damage","message":"已确认片源尾部损坏，末尾部分没有进度预览"}
        }""").put("pages", JSONArray((0 until 37).map { "/api/stream/previews/cache/tail/page_%04d.jpg".format(it) }))

    @Test fun confirmedMissingTailPreservesOriginalTimelineAndNeverExtendsTheLastFrame() {
        val value = tailManifest()
        val manifest = SeekPreviewManifest.fromJson(value)!!
        assertEquals(945, manifest.count)
        assertEquals(9449.147, manifest.coverage!!.duration, 0.0)
        assertEquals(925, manifest.coverage.availableCount)
        assertEquals(SeekPreviewFrame("/api/stream/previews/cache/tail/page_0036.jpg", 1920, 1080, 480, 270),
            manifest.frameAt(9249.999))
        for (time in listOf(9250.0, 9313.0, 9449.147, 20000.0, Double.MAX_VALUE,
                            Double.POSITIVE_INFINITY, Double.NaN, -1.0)) {
            assertNull("No preview is available at $time", manifest.frameAt(time))
        }
        assertFalse(manifest.missingTailAt(9249.999))
        assertTrue(manifest.missingTailAt(9250.0))
        assertFalse(manifest.missingTailAt(Double.NaN))
    }

    @Test fun readyTailWarningAndCoverageRemainVisibleWithoutOfferingGenerationAgain() {
        val status = SeekPreviewStatus.fromJson(tailManifest())
        assertEquals("ready", status.state)
        assertEquals("进度预览已生成", status.actionLabel)
        assertFalse(status.canGenerate)
        assertEquals("已确认片源尾部损坏，末尾部分没有进度预览", status.warning)
        assertEquals("预览覆盖至 2:34:10 / 2:37:29", status.coverageDescription)
        assertNull(status.seekNotice(9249.999))
        assertEquals("片尾缺少预览", status.seekNotice(9250.0))
        assertEquals("片尾缺少预览", status.seekNotice(9313.0))
    }

    @Test fun incompleteCoverageDeclarationsCannotFallBackToLegacyClamping() {
        val required = listOf("state", "completion", "available_count", "coverage_end", "duration", "warning")
        for (field in required) {
            val value = tailManifest().apply { remove(field) }
            assertNull("Missing $field must invalidate declared coverage", SeekPreviewManifest.fromJson(value))
            val status = SeekPreviewStatus.fromJson(value)
            assertEquals("failed", status.state)
            assertNull(status.manifest)
            assertEquals("进度预览覆盖信息无效，请稍后重试", status.error)
            assertNull(status.warning)
            assertNull(status.coverageDescription)
        }
        for (field in listOf("code", "message")) {
            val value = tailManifest().apply { getJSONObject("warning").remove(field) }
            assertNull("Incomplete warning $field", SeekPreviewManifest.fromJson(value))
        }
        val legacy = tailManifest().apply {
            remove("completion"); remove("available_count"); remove("coverage_end"); remove("warning")
        }
        for (field in listOf("completion", "available_count", "coverage_end", "warning")) {
            val value = JSONObject(legacy.toString()).put(field, tailManifest().get(field))
            assertNull("Lone $field must not silently lose coverage", SeekPreviewManifest.fromJson(value))
        }
        for (invalid in listOf(JSONObject.NULL, "unknown")) {
            assertNull(SeekPreviewManifest.fromJson(JSONObject(legacy.toString()).put("completion", invalid)))
        }
    }

    @Test fun invalidCoverageNumbersAndPageCountsAreRejected() {
        val mutations = listOf<Pair<String, Any>>(
            "available_count" to 0, "available_count" to -1, "available_count" to 945,
            "available_count" to 946, "available_count" to 925.5, "available_count" to "925",
            "available_count" to JSONObject.NULL,
            "coverage_end" to 0, "coverage_end" to -1, "coverage_end" to 9249.999,
            "coverage_end" to 9250.001, "coverage_end" to 9449.147, "coverage_end" to "9250",
            "coverage_end" to "NaN", "coverage_end" to "Infinity", "coverage_end" to JSONObject.NULL,
            "duration" to 0, "duration" to -1, "duration" to 9250,
            "duration" to "9449.147", "duration" to JSONObject.NULL,
            "duration" to java.math.BigDecimal("1e999"),
            "count" to 944, "count" to 945.5, "count" to "945", "count" to 4_294_968_241L,
            "state" to "partial", "completion" to "unknown",
        )
        for ((field, invalid) in mutations) {
            val value = tailManifest().put(field, invalid)
            assertNull("Invalid $field=$invalid", SeekPreviewManifest.fromJson(value))
            assertNull(SeekPreviewStatus.fromJson(value).manifest)
        }
        for (pageCount in listOf(0, 36, 38)) {
            val value = tailManifest().put("pages", JSONArray((0 until pageCount).map {
                "/api/stream/previews/cache/tail/page_%04d.jpg".format(it)
            }))
            assertNull("Pages must match available_count", SeekPreviewManifest.fromJson(value))
        }
        assertNotNull(SeekPreviewManifest.fromJson(tailManifest().put("available_count", 925L)))
    }

    @Test fun legacyManifestsKeepTheirSamplingAndStatusWithoutCoverageWarnings() {
        val value = JSONObject("""{"state":"ready","interval":10,"count":26,"width":160,"height":90,
            "columns":5,"rows":5,"pages":["/api/stream/previews/cache/old/page_0000.jpg",
            "/api/stream/previews/cache/old/page_0001.jpg"]}""")
        val status = SeekPreviewStatus.fromJson(value)
        assertEquals("ready", status.state)
        assertNull(status.manifest!!.coverage)
        assertEquals(status.manifest.frameAt(250.0), status.manifest.frameAt(20000.0))
        assertNull(status.warning)
        assertNull(status.coverageDescription)
        assertNull(status.seekNotice(20000.0))
        val running = SeekPreviewStatus.fromJson(value.put("state", "running"))
        assertEquals("正在生成进度预览", running.actionLabel)
        assertFalse(running.canGenerate)
        val failed = SeekPreviewStatus.fromJson(value.put("state", "failed").put("error", "重试失败"))
        assertEquals("继续生成进度预览", failed.actionLabel)
        assertEquals("重试失败", failed.error)
        assertTrue(failed.canGenerate)
    }

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
