package org.jzmedia.tv.data

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class OnlineModelsTest {
    private val source = "src_" + "1".repeat(32)
    private val item = "itm_" + "2".repeat(32)
    private val line = "ln_" + "3".repeat(32)
    private val episode = "ep_" + "4".repeat(32)
    private val sid = "s".repeat(43)
    private val movie = OnlineIdentity(source, item, "movie", lineId = line)
    private fun sourceJson() = JSONObject().put("id", source).put("name", "合成来源").put("provider", "maccms")
    private fun searchItem() = JSONObject().put("external_id", "show-1").put("title", "合成剧集").put("year", "2026")
        .put("kind", "tv").put("description", "独立测试资料")
    private fun searchPage() = JSONObject().put("source", sourceJson()).put("items", JSONArray().put(searchItem()))
        .put("page", 1).put("page_count", 1).put("total", 1)
    private fun descriptor() = JSONObject().put("identity", movie.toJson()).put("session_id", sid)
        .put("media_url", "/api/online/sessions/$sid/media").put("transport", "mp4")
        .put("processing", "passthrough").put("seekable", true).put("duration_seconds", 24.0)
        .put("time_offset_seconds", 0.0).put("expires_at", 2_147_483_648L)
    private fun longDetail(vararg counts: Int): JSONObject {
        val lines = JSONArray()
        counts.forEachIndexed { lineIndex, count ->
            val lineId = "ln_" + lineIndex.toString(16).padStart(32, '0')
            val entries = JSONArray()
            repeat(count) { index ->
                val identity = OnlineIdentity(source, item, "episode",
                    "ep_" + (lineIndex * 10000 + index).toString(16).padStart(32, '0'), lineId)
                entries.put(JSONObject().put("label", "第${index + 1}期").put("identity", identity.toJson())
                    .put("transport", "hls").put("supported", true).put("reason", ""))
            }
            lines.put(JSONObject().put("id", lineId).put("label", "线路${lineIndex + 1}").put("entries", entries))
        }
        return JSONObject().put("source", JSONObject().put("id", source).put("name", "合成来源").put("provider", "maccms"))
            .put("item", JSONObject().put("id", item).put("source_id", source).put("kind", "tv").put("title", "合成长综艺"))
            .put("description", "").put("year", "2026").put("lines", lines).put("attribution", JSONArray())
    }

    @Test fun onlineFeatureRequiresAnExplicitServerFeature() {
        assertFalse(supportsOnlineVod(JSONObject()))
        assertFalse(supportsOnlineVod(JSONObject().put("version", "99.0.0")))
        assertTrue(supportsOnlineVod(JSONObject().put("features", JSONArray().put("online_vod"))))
        assertTrue(supportsOnlineVod(JSONObject().put("features", JSONObject().put("online_vod", true))))
        assertFalse(supportsOnlineVod(JSONObject().put("features", JSONObject().put("online_vod", "true"))))
        assertFalse(supportsOnlineVod(JSONObject().put("features", JSONArray().put("viewing_modes"))))
    }

    @Test fun oldCatalogueResponsesKeepSearchWithoutInventingBrowseAndYearCapabilities() {
        val old = OnlineSource.fromJson(sourceJson())
        assertFalse(old.browseSupported)
        assertFalse(old.yearFilterSupported)
        val current = OnlineSource.fromJson(sourceJson().put("capabilities", JSONObject().put("browse", true).put("year_filter", false)))
        assertTrue(current.browseSupported)
        assertFalse(current.yearFilterSupported)
        val filtered = OnlineSource.fromJson(sourceJson().put("capabilities", JSONObject().put("year_filter", true)))
        assertTrue(filtered.yearFilterSupported)
        assertFalse(filtered.browseSupported)
        assertThrows(IllegalArgumentException::class.java) {
            OnlineSource.fromJson(sourceJson().put("capabilities", JSONObject().put("browse", "true")))
        }
        val page = OnlineSearchPage.fromJson(searchPage())
        assertTrue(page.categories.isEmpty())
        val entry = page.items.single()
        assertNull(entry.posterPath)
        assertEquals("", entry.category)
        assertEquals("", entry.categoryId)
        assertEquals("", entry.parentCategoryId)
        assertEquals("", entry.remarks)
    }

    @Test fun browseKeepsSourceTaxonomyMetadataAndOnlyTheSourceBoundArtworkLease() {
        val path = "/api/online/catalog/artwork/$source/" + "a".repeat(32)
        val row = searchItem().put("poster_path", path).put("category", "国产剧").put("category_id", "13")
            .put("parent_category_id", "2").put("remarks", "更新至第 2 集")
        val tree = JSONArray().put(JSONObject().put("id", "2").put("name", "剧集"))
            .put(JSONObject().put("id", "13").put("name", "国产剧").put("parent_id", "2"))
        val page = OnlineSearchPage.fromJson(searchPage().put("items", JSONArray().put(row)).put("categories", tree))
        assertEquals(path, page.items.single().posterPath)
        assertEquals("国产剧", page.items.single().category)
        assertEquals("13", page.items.single().categoryId)
        assertEquals("2", page.items.single().parentCategoryId)
        assertEquals("更新至第 2 集", page.items.single().remarks)
        assertEquals(listOf(OnlineCategory("2", "剧集"), OnlineCategory("13", "国产剧", "2")), page.categories)
        assertNull(OnlineSearchItem.fromJson(searchItem().put("poster_path", "")).posterPath)
        assertNull(OnlineSearchItem.fromJson(searchItem().put("poster_path", JSONObject.NULL)).posterPath)
    }

    @Test fun catalogueInvalidationTokensRemainOptionalForOlderServersAndKeepCurrentOpaqueBindings() {
        val old = OnlineSources.fromJson(JSONObject().put("enabled", true).put("items", JSONArray().put(sourceJson())))
        assertEquals("", old.scopeToken)
        assertEquals("", old.items.single().configurationToken)
        val current = OnlineSources.fromJson(JSONObject().put("enabled", true).put("scope_token", "a".repeat(64))
            .put("items", JSONArray().put(sourceJson().put("configuration_token", "b".repeat(64)))))
        assertEquals("a".repeat(64), current.scopeToken)
        assertEquals("b".repeat(64), current.items.single().configurationToken)
        val restarted = OnlineSources.fromJson(JSONObject().put("enabled", true).put("scope_token", "c".repeat(64))
            .put("items", JSONArray().put(sourceJson().put("configuration_token", "d".repeat(64)))))
        assertNotEquals(current.scopeToken, restarted.scopeToken)
        assertNotEquals(current.items.single().configurationToken, restarted.items.single().configurationToken)
        assertEquals("", OnlineSource.fromJson(sourceJson().put("configuration_token", "")).configurationToken)
    }

    @Test fun catalogueInvalidationTokensRejectMalformedValuesInsteadOfWeakeningTheBinding() {
        for (invalid in listOf("a".repeat(63), "a".repeat(65), "A".repeat(64), "g".repeat(64), "a".repeat(64) + "\n", 42)) {
            assertThrows(IllegalArgumentException::class.java) { OnlineSource.fromJson(sourceJson().put("configuration_token", invalid)) }
            assertThrows(IllegalArgumentException::class.java) {
                OnlineSources.fromJson(JSONObject().put("enabled", true).put("items", JSONArray()).put("scope_token", invalid))
            }
        }
    }

    @Test fun artworkNeverAllowsExternalUrlsOtherSourceLeasesOrExtraPathComponents() {
        val path = "/api/online/catalog/artwork/$source/" + "a".repeat(32)
        val other = "src_" + "2".repeat(32)
        for (invalid in listOf("https://foreign.example/a.jpg", "//foreign.example/a.jpg", "/api/artwork/a.jpg",
            path + "?token=secret", path + "#fragment", path + "/../media", path.replace(source, other),
            path.replace("artwork/", "artwork%2f"), path.dropLast(1), path + "\n", 42)) {
            assertThrows("must reject artwork $invalid", IllegalArgumentException::class.java) {
                OnlineSearchPage.fromJson(searchPage().put("items", JSONArray().put(searchItem().put("poster_path", invalid))))
            }
        }
    }

    @Test fun detailPosterUsesOnlyTheDetailSourceLeaseAndOldServersCanOmitIt() {
        val empty = longDetail(1)
        assertNull(OnlineDetail.fromJson(empty).posterPath)
        assertNull(OnlineDetail.fromJson(longDetail(1).put("poster_path", "")).posterPath)
        val path = "/api/online/catalog/artwork/$source/" + "a".repeat(32)
        assertEquals(path, OnlineDetail.fromJson(longDetail(1).put("poster_path", path)).posterPath)
        val other = "src_" + "2".repeat(32)
        for (invalid in listOf(path.replace(source, other), "https://foreign.example/image.png", path + "?x=1", path + "\n")) {
            assertThrows(IllegalArgumentException::class.java) { OnlineDetail.fromJson(longDetail(1).put("poster_path", invalid)) }
        }
    }

    @Test fun categoryTreesRemainBoundedAndRejectDuplicateOrMalformedIdentities() {
        val categories = JSONArray()
        repeat(256) { categories.put(JSONObject().put("id", "category-$it").put("name", "分类 $it")) }
        assertEquals(256, OnlineSearchPage.fromJson(searchPage().put("categories", categories)).categories.size)
        categories.put(JSONObject().put("id", "overflow").put("name", "超出上限"))
        assertThrows(IllegalArgumentException::class.java) { OnlineSearchPage.fromJson(searchPage().put("categories", categories)) }
        val category = JSONObject().put("id", "13").put("name", "国产剧")
        assertThrows(IllegalArgumentException::class.java) {
            OnlineSearchPage.fromJson(searchPage().put("categories", JSONArray().put(category).put(category)))
        }
        for ((field, value) in listOf("id" to "../13", "name" to " ", "parent_id" to "13&query=1")) {
            assertThrows(IllegalArgumentException::class.java) { OnlineCategory.fromJson(JSONObject(category.toString()).put(field, value)) }
        }
        assertThrows(IllegalArgumentException::class.java) { OnlineSearchItem.fromJson(searchItem().put("category_id", "13&query=1")) }
    }

    @Test fun onlineIdentityCannotBeConfusedWithLocalMediaOrAnotherEpisode() {
        assertEquals(movie, OnlineIdentity.fromJson(movie.toJson()))
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity("1", item, "movie") }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity(source, item, "tv", episode) }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity(source, item, "movie", episode) }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity(source, item, "episode") }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity(source, item, "movie", lineId = "https://cdn.example/file.mp4") }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity.fromJson(movie.toJson().put("media_id", 7)) }
        assertThrows(IllegalArgumentException::class.java) { OnlineIdentity.fromJson(movie.toJson().put("source_id", 1)) }
    }

    @Test fun providerExternalIdentityRetainsUnicodeSpacesAndAmpersandsWithinTheServerLimit() {
        assertEquals("章节一 & 02", checkedOnlineExternal(" 章节一 & 02 "))
        assertEquals("影".repeat(512), checkedOnlineExternal("影".repeat(512)))
        val supplementary = "\uD83C\uDFAC".repeat(512)
        assertEquals(supplementary, checkedOnlineExternal(supplementary))
        for (invalid in listOf(" ", "x\n", "a\u007fb", "影".repeat(513), supplementary + "a")) {
            assertThrows(IllegalArgumentException::class.java) { checkedOnlineExternal(invalid) }
        }
    }

    @Test fun playbackDescriptorAcceptsOnlyAnOwnedPassthroughPathAndOriginalTimeline() {
        assertEquals("mp4", OnlineDescriptor.fromJson(descriptor(), movie).transport)
        assertEquals(24.0, OnlineDescriptor.fromJson(descriptor(), movie).durationSeconds!!, 0.0)
        val corruptions = listOf(
            "media_url" to "https://cdn.example/file.mp4", "media_url" to "//other.example/file.mp4",
            "media_url" to "/api/stream/sessions/$sid/media", "media_url" to "/api/online/sessions/$sid/media?token=secret",
            "processing" to "transcode", "time_offset_seconds" to 12, "time_offset_seconds" to "0",
            "seekable" to "true", "transport" to "dash", "duration_seconds" to -1,
            "duration_seconds" to "24", "expires_at" to "2147483648", "session_id" to "../other",
            "headers" to JSONObject().put("Cookie", "secret"),
        )
        corruptions.forEach { (field, value) ->
            assertThrows("must reject $field", IllegalArgumentException::class.java) {
                OnlineDescriptor.fromJson(descriptor().put(field, value), movie)
            }
        }
        assertThrows(IllegalArgumentException::class.java) {
            OnlineDescriptor.fromJson(descriptor(), movie.copy(lineId = "ln_" + "9".repeat(32)))
        }
    }

    @Test fun freshDetailNeverSkipsAnUnsupportedNextEpisodeOrChangesSourceAndLine() {
        val first = OnlineIdentity(source, item, "episode", episode, line)
        val second = first.copy(episodeId = "ep_" + "5".repeat(32))
        val third = first.copy(episodeId = "ep_" + "6".repeat(32))
        val entries = listOf(OnlineEntry("1", first, "mp4", true, ""), OnlineEntry("2", second, null, false, "unsupported"),
            OnlineEntry("3", third, "hls", true, ""))
        val detail = OnlineDetail(OnlineSource(source, "Source", "maccms"), OnlineCatalogItem(item, source, "tv", "Show"),
            "", "", listOf(OnlineLine(line, "Line", entries)))
        assertNull(detail.nextEpisode(first))
        assertEquals(third, detail.nextEpisode(second)?.identity)
        assertNull(detail.nextEpisode(third))
        assertNull(detail.nextEpisode(first.copy(lineId = "ln_" + "9".repeat(32))))
        assertNull(detail.copy(source = detail.source.copy(id = "src_" + "8".repeat(32))).nextEpisode(first))
        assertEquals(first, detail.findEntry(first)?.identity)
        assertNull(detail.findEntry(second))
    }

    @Test fun longSingleLineRetainsLateEpisodeIdentitiesWithinTheExistingTotalBudget() {
        for (count in listOf(501, 783, 2000)) {
            val detail = OnlineDetail.fromJson(longDetail(count))
            val entries = detail.lines.single().entries
            assertEquals(count, entries.size)
            assertEquals("第${count}期", entries.last().label)
            val last = entries.last().identity!!
            assertEquals("ep_" + (count - 1).toString(16).padStart(32, '0'), last.episodeId)
            assertEquals(entries.last(), detail.findEntry(last))
            assertEquals(last, detail.nextEpisode(entries[count - 2].identity!!)?.identity)
            assertNull(detail.nextEpisode(last))
        }
    }

    @Test fun entryBudgetRemainsTwoThousandAcrossAllLines() {
        assertEquals(2000, OnlineDetail.fromJson(longDetail(1000, 1000)).lines.sumOf { it.entries.size })
        assertThrows(IllegalArgumentException::class.java) { OnlineDetail.fromJson(longDetail(2001)) }
        assertThrows(IllegalArgumentException::class.java) { OnlineDetail.fromJson(longDetail(1000, 1001)) }
        val oversizedLine = longDetail(2001).getJSONArray("lines").getJSONObject(0)
        assertThrows(IllegalArgumentException::class.java) { OnlineLine.fromJson(oversizedLine) }
    }

    @Test fun errorsUseTrustedCodesAndNeverReflectArbitraryServerText() {
        assertTrue(onlineErrorMessage(ApiException(409, "proxy password", "online_progress_writer_conflict")).contains("另一播放页"))
        assertTrue(onlineErrorMessage(ApiException(404, "server path secret")).contains("升级服务端"))
        assertTrue(onlineErrorMessage(ApiException(404, "secret", "online_identity_not_found")).contains("线路"))
        assertFalse(onlineErrorMessage(ApiException(502, "proxy user:password")).contains("password"))
        assertEquals("视频源返回的分集数量超过读取上限，请选择其他内容或来源。",
            onlineErrorMessage(ApiException(502, "proxy password", "provider_entry_limit")))
        assertEquals("视频源返回的资料过大，暂时无法读取。",
            onlineErrorMessage(ApiException(502, "proxy password", "provider_response_limit")))
        assertFalse(onlineErrorMessage(IllegalArgumentException("secret")).contains("secret"))
    }
}
