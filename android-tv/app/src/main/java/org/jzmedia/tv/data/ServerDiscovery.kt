package org.jzmedia.tv.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.supervisorScope
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.TimeUnit

/** 局域网内发现的一台 jzmedia 服务器（Docker 网桥收不到广播，故由电视端主动扫描）。 */
data class DiscoveredServer(
    val address: String,
    val name: String,
    val version: String,
    val serverId: String,
    val authRequired: Boolean,
    /** protocol_version == 1；false 时仅展示并提示升级服务端，不可连接。 */
    val protocolOk: Boolean,
)

object ServerDiscovery {
    const val DEFAULT_PORT = 8080
    const val PROTOCOL_VERSION = 1

    /** 本机 IPv4 站点地址（以太网/WLAN 双栈只取 v4，纯 v6 设备返回空列表即提示手动填写）。 */
    fun localIpv4(): List<String> {
        val out = mutableListOf<String>()
        try {
            val ifs = NetworkInterface.getNetworkInterfaces() ?: return out
            for (nic in ifs) {
                try {
                    if (!nic.isUp || nic.isLoopback) continue
                } catch (_: Exception) { continue }
                val addrs = nic.inetAddresses ?: continue
                for (addr in addrs) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress && addr.isSiteLocalAddress) {
                        out.add(addr.hostAddress ?: continue)
                    }
                }
            }
        } catch (_: Exception) { /* 无网络时返回空，UI 提示手动填写 */ }
        return out.distinct()
    }

    /** 由本机 IPv4 推导同网段候选（/24，排除网络/广播/本机地址）。 */
    fun candidatesFor(localIp: String, port: Int = DEFAULT_PORT): List<String> {
        val parts = localIp.trim().split(".")
        if (parts.size != 4) return emptyList()
        val nums = parts.map { it.toIntOrNull() ?: return emptyList() }
        if (nums.any { it !in 0..255 }) return emptyList()
        val prefix = nums.take(3).joinToString(".")
        return (1..254).filter { it != nums[3] }.map { "http://$prefix.$it:$port/" }
    }

    /** 解析握手响应；不是 jzmedia 或字段缺失返回 null（静默跳过）。 */
    fun parse(address: String, body: String): DiscoveredServer? {
        val json = try { JSONObject(body) } catch (_: Exception) { return null }
        if (!json.has("protocol_version") || !json.has("server_id")) return null
        return DiscoveredServer(
            address = address,
            name = json.optString("name").ifBlank { "jzmedia" },
            version = json.optString("version").ifBlank { "未知版本" },
            serverId = json.optString("server_id"),
            authRequired = json.optBoolean("auth_required"),
            protocolOk = json.optInt("protocol_version", -1) == PROTOCOL_VERSION,
        )
    }

    /** 按 serverId 认亲：已保存服务器换了 IP 时定位新地址。 */
    fun matchById(found: List<DiscoveredServer>, serverId: String?): DiscoveredServer? {
        if (serverId.isNullOrBlank()) return null
        return found.firstOrNull { it.serverId == serverId }
    }

    private val probeClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(700, TimeUnit.MILLISECONDS)
            .readTimeout(1200, TimeUnit.MILLISECONDS)
            .callTimeout(2500, TimeUnit.MILLISECONDS)
            .followRedirects(false).followSslRedirects(false).build()
    }

    private fun httpProbe(address: String): DiscoveredServer? {
        return try {
            val request = Request.Builder().url(address + "api/stream/client-info")
                .header("Accept", "application/json").build()
            probeClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return null
                val text = response.body?.string().orEmpty()
                if (text.length > 8192) return null
                parse(address, text)
            }
        } catch (_: Exception) { null }
    }

    /** 扫描本机所在网段；onProgress 已检查数/总数（UI 进度用，可取消）。 */
    suspend fun scan(
        locals: List<String> = localIpv4(),
        port: Int = DEFAULT_PORT,
        maxParallel: Int = 24,
        probe: suspend (String) -> DiscoveredServer? = { httpProbe(it) },
        onProgress: (done: Int, total: Int) -> Unit = { _, _ -> },
    ): List<DiscoveredServer> = withContext(Dispatchers.IO) {
        val targets = locals.flatMap { candidatesFor(it, port) }.distinct()
        if (targets.isEmpty()) return@withContext emptyList()
        val found = mutableListOf<DiscoveredServer>()
        var done = 0
        supervisorScope {
            targets.chunked(maxParallel).forEach { chunk ->
                val jobs = chunk.map { target ->
                    async {
                        val hit = try { probe(target) } catch (_: Exception) { null }
                        synchronized(found) {
                            if (hit != null) found.add(hit)
                            done++
                            onProgress(done, targets.size)
                        }
                    }
                }
                jobs.awaitAll()
            }
        }
        found.sortedBy { it.address }
    }
}
