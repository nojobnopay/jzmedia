package org.jzmedia.tv.data

import java.io.IOException
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class OnlineHostsTest {
    private val source = "src_" + "1".repeat(32)
    private val fingerprint = "a".repeat(64)
    private fun report(missing: List<String> = listOf("media.example"), allowed: List<String> = listOf("api.example")) =
        JSONObject().put("source_id", source).put("external_id", "show-1")
            .put("missing_hosts", JSONArray(missing)).put("allowed_hosts", JSONArray(allowed))
            .put("rejected_count", 0).put("configuration_fingerprint", fingerprint)
    private fun invalid(value: JSONObject) = assertThrows(IllegalArgumentException::class.java) {
        OnlineHostReport.fromJson(value)
    }

    @Test fun reportContainsOnlyCanonicalNamesAndKeepsLegacyApiHostsSeparateFromCandidates() {
        val allowed = listOf("api", "localhost", "api.local", "api.localhost", "192.168.1.2", "fd00::1")
        val candidates = listOf("xn--bcher-kva.example", "media.example", "8.8.8.8", "2606:4700:4700::1111")
        val value = OnlineHostReport.fromJson(report(candidates, allowed))
        assertEquals(candidates, value.missingHosts)
        assertEquals(allowed, value.allowedHosts)
        assertEquals(source, value.sourceId)
        assertEquals("show-1", value.externalId)
        assertEquals(fingerprint, value.configurationFingerprint)
        assertEquals(0, value.rejectedCount)
    }

    @Test fun reportRequiresExactlySixTypedFieldsAndValidIdentities() {
        invalid(report().put("url", "https://secret.example/private"))
        invalid(report().apply { remove("allowed_hosts") })
        invalid(report().put("source_id", "../../sources"))
        invalid(report().put("external_id", "show-1&url=secret"))
        invalid(report().put("external_id", "x".repeat(129)))
        invalid(report().put("configuration_fingerprint", "A".repeat(64)))
        invalid(report().put("configuration_fingerprint", "a".repeat(63)))
        for (count in listOf(-1, 0.0, "0", true, Int.MAX_VALUE.toLong() + 1)) {
            invalid(report().put("rejected_count", count))
        }
        invalid(report().put("missing_hosts", "media.example"))
        invalid(report().put("allowed_hosts", JSONArray().put(123)))
    }

    @Test fun existingMappedIpv6RemainsUnchangedWhileANewMediaHostCanBeApproved() {
        val allowed = listOf("api.example", "::ffff:808:808")
        val value = OnlineHostReport.fromJson(report(allowed = allowed))
        assertEquals(allowed, value.allowedHosts)
        val selected = checkedHostApproval(value, listOf("media.example"))
        checkedHostApprovalResponse(JSONObject().put("id", source)
            .put("allowed_hosts", JSONArray(allowed + selected)), value, selected)
        invalid(report(missing = listOf("::ffff:808:808")))
        invalid(report(allowed = listOf("::ffff:8.8.8.8")))
    }

    @Test fun reportRejectsDuplicateOverlappingAndOversizedLists() {
        invalid(report(listOf("media.example", "media.example")))
        invalid(report(allowed = listOf("api.example", "api.example")))
        invalid(report(listOf("api.example")))
        val limit = (1..128).map { "cdn$it.example" }
        assertEquals(128, OnlineHostReport.fromJson(report(limit)).missingHosts.size)
        invalid(report(limit + "extra.example"))
        invalid(report(allowed = limit + "extra.example"))
    }

    @Test fun candidateHostsRejectUrlsAuthorityConfusionAndNoncanonicalFormsWithoutDns() {
        for (host in listOf("https://media.example/path", "user@media.example", "media.example:443", "*.example",
            "media.example/path", "media.example?token=private", "media.example#private", "media.example\\path",
            "MEDIA.EXAMPLE", "media.example.", "media..example", "-media.example", "media_.example", "bücher.example",
            "media.example\n", "a".repeat(64) + ".example", "01.2.3.4", "2130706433", "0x7f000001", "127.1",
            "[2606:4700:4700::1111]", "2606:4700:4700:0:0:0:0:1111")) {
            invalid(report(listOf(host)))
        }
    }

    @Test fun missingCandidatesCannotBeSingleLabelOrObviousPrivateAndLocalLiterals() {
        for (host in listOf("nas", "localhost", "0.0.0.0", "10.0.0.1", "127.0.0.1",
            "100.64.0.1", "169.254.169.254", "172.16.0.1", "192.168.1.1", "224.0.0.1", "255.255.255.255", "::1", "fc00::1",
            "fe80::1", "ff02::1", "::ffff:808:808", "64:ff9b::808:808")) {
            invalid(report(listOf(host)))
        }
    }

    @Test fun approvalRequiresANonemptyUniqueSubsetOfThisExactReport() {
        val value = OnlineHostReport.fromJson(report(listOf("media.example", "images.example")))
        assertEquals(listOf("images.example"), checkedHostApproval(value, listOf("images.example")))
        for (selected in listOf(emptyList(), listOf("media.example", "media.example"), listOf("elsewhere.example"), listOf("api.example"))) {
            assertThrows(IllegalArgumentException::class.java) { checkedHostApproval(value, selected) }
        }
    }

    @Test fun approvalResponseMustPreserveExistingAndSelectedHostsAndBelongToThisSource() {
        val value = OnlineHostReport.fromJson(report())
        fun response(id: String = source, hosts: List<String> = listOf("api.example", "media.example")) =
            JSONObject().put("id", id).put("allowed_hosts", JSONArray(hosts))
                .put("api_base_url", "https://private.example/api").put("network", JSONObject().put("secret", "not-used"))
        checkedHostApprovalResponse(response(), value, listOf("media.example"))
        assertThrows(IllegalArgumentException::class.java) {
            checkedHostApprovalResponse(response("src_" + "2".repeat(32)), value, listOf("media.example"))
        }
        for (hosts in listOf(listOf("api.example"), listOf("media.example"), listOf("api.example", "media.example", "media.example"))) {
            assertThrows(IllegalArgumentException::class.java) { checkedHostApprovalResponse(response(hosts = hosts), value, listOf("media.example")) }
        }
    }

    @Test fun errorsKeepSaveUncertaintyAndConfirmedSaveSeparateWithoutReflectingSecrets() {
        val unknown = onlineHostError(ApiException(0, "private credential"), applying = true)
        assertTrue(unknown.contains("无法确认"))
        assertTrue(onlineHostError(IOException("private credential"), applying = true).contains("无法确认"))
        assertTrue(onlineHostError(ApiException(500, "private credential"), applying = true).contains("无法确认"))
        assertTrue(onlineHostError(ApiException(409, "private credential", "online_source_changed"), applying = true).contains("重新检查"))
        assertTrue(onlineHostError(ApiException(404, "private credential")).contains("升级服务端"))
        assertTrue(onlineHostError(ApiException(403, "private credential", "online_management_revoked")).contains("验证管理权限"))
        assertTrue(onlineHostError(ApiException(422, "private credential", "online_allowed_host_limit")).contains("上限"))
        val saved = onlineHostError(IOException("private credential"), saved = true, applying = true)
        assertTrue(saved.contains("已添加") && saved.contains("不必重复添加"))
        assertFalse((unknown + saved).contains("private credential"))
    }
}
