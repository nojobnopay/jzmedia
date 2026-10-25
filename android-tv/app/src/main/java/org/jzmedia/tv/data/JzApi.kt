package org.jzmedia.tv.data

import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONObject
import org.jzmedia.tv.BuildConfig
import java.io.IOException
import java.io.InterruptedIOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class ApiException(val status: Int, message: String, val code: String = "") : IOException(message)

/** One server origin per connection. Redirects deliberately never carry credentials elsewhere. */
class JzApi(address: String, val token: String = "", viewing: ViewingSession? = null,
            private val initialViewingToken: String = "") {
    var viewing: ViewingSession? by mutableStateOf(viewing)
        private set
    fun updateViewingMetadata(session: ViewingSession) {
        check(viewing?.mode == session.mode && viewing?.generation == session.generation && viewingToken == session.token)
        viewing = session
    }
    private val locked = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val viewingLocked = locked.asSharedFlow()
    val viewingToken: String get() = viewing?.token ?: initialViewingToken
    val cacheScope: String = java.util.UUID.randomUUID().toString()
    private val privateSearchEntries = mutableMapOf<String, String>()
    val privateHistory: SearchHistoryStore by lazy {
        SearchHistoryStore({ privateSearchEntries[it] }, { key, value ->
            if (value == null) privateSearchEntries.remove(key) else privateSearchEntries[key] = value
        })
    }
    fun clearPrivateHistory() { privateSearchEntries.clear() }
    val baseUrl: String = normalizeServerUrl(address)
    private val base: HttpUrl = baseUrl.toHttpUrlOrNull()!!
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .callTimeout(20, TimeUnit.SECONDS)
        .followRedirects(false).followSslRedirects(false).build()
    // Probe/transcode/subtitle extraction may legitimately take longer than browsing.
    private val playbackClient = client.newBuilder()
        .readTimeout(150, TimeUnit.SECONDS).callTimeout(180, TimeUnit.SECONDS).build()

    init {
        require(token.length <= 4096 && token.none { it == '\r' || it == '\n' }) { "访问令牌格式无效" }
        require(viewingToken.length <= 4096 && viewingToken.none { it == '\r' || it == '\n' }) { "观看模式会话格式无效" }
    }

    fun absoluteUrl(path: String): String {
        require(!path.contains('\\')) { "服务器资源地址无效" }
        val url = if (path.startsWith("http://") || path.startsWith("https://")) {
            path.toHttpUrlOrNull()
        } else {
            require(!path.startsWith("//")) { "服务器资源地址无效" }
            base.resolve(path.trimStart('/'))
        } ?: throw IllegalArgumentException("服务器资源地址无效")
        require(isAllowed(url)) { "资源地址不属于当前服务器" }
        return url.toString()
    }

    fun authHeadersFor(url: String): Map<String, String> {
        val target = url.toHttpUrlOrNull() ?: return emptyMap()
        if (!isAllowed(target)) return emptyMap()
        return buildMap {
            if (token.isNotEmpty()) put("X-Api-Token", token)
            put("X-Viewing-Client", "tv")
            if (viewingToken.isNotEmpty()) put("X-Viewing-Session", viewingToken)
        }
    }

    private fun isAllowed(url: HttpUrl): Boolean =
        url.scheme == base.scheme && url.host == base.host && url.port == base.port &&
            url.username.isEmpty() && url.password.isEmpty() && url.fragment == null &&
            url.encodedPath.startsWith(base.encodedPath)

    suspend fun get(path: String): JSONObject = json("GET", path)
    suspend fun post(path: String, body: JSONObject = JSONObject()): JSONObject = json("POST", path, body)
    suspend fun getPlayback(path: String): JSONObject = json("GET", path, longRequest = true)
    suspend fun postPlayback(path: String, body: JSONObject = JSONObject()): JSONObject = json("POST", path, body, longRequest = true)
    suspend fun delete(path: String): JSONObject = json("DELETE", path)
    suspend fun patch(path: String, body: JSONObject): JSONObject = json("PATCH", path, body)
    suspend fun postReceiver(path: String, receiverToken: String, body: JSONObject): JSONObject =
        json("POST", path, body, receiverToken = checkedReceiverToken(receiverToken))
    suspend fun deleteReceiver(path: String, receiverToken: String): JSONObject =
        json("DELETE", path, receiverToken = checkedReceiverToken(receiverToken))
    private fun checkedReceiverToken(value: String): String = value.also {
        require(it.isNotBlank() && it.length <= 512 && it.none { c -> c == '\r' || c == '\n' }) { "电视接收凭据无效" }
    }
    fun readViewingSession(value: JSONObject): ViewingSession = try { ViewingSession.fromJson(value, viewingToken) }
    catch (e: ApiException) { if (e.code == "viewing_locked") locked.tryEmit(Unit); throw e }
    fun withViewing(value: JSONObject): JzApi = JzApi(baseUrl, token, readViewingSession(value))
    fun cancelRequests() { client.dispatcher.cancelAll(); playbackClient.dispatcher.cancelAll() }
    suspend fun getText(path: String): String = execute("GET", path, null, 16 * 1024 * 1024, longRequest = true).toString(Charsets.UTF_8)
    suspend fun getBytes(path: String): ByteArray = execute("GET", path, null, 4 * 1024 * 1024)
    // CJK font collections can exceed the artwork limit; first access extracts MKV attachments.
    suspend fun getFontBytes(path: String): ByteArray = execute("GET", path, null, 32 * 1024 * 1024, longRequest = true)

    private suspend fun json(method: String, path: String, body: JSONObject? = null, longRequest: Boolean = false,
                             receiverToken: String? = null): JSONObject {
        val text = execute(method, path, body, 8 * 1024 * 1024, longRequest, receiverToken).toString(Charsets.UTF_8)
        return try { if (text.isBlank()) JSONObject() else JSONObject(text) }
        catch (_: Exception) { throw ApiException(0, "服务器返回了无法识别的数据") }
    }

    private suspend fun execute(method: String, path: String, body: JSONObject?, maxBytes: Int, longRequest: Boolean = false,
                                receiverToken: String? = null): ByteArray =
        suspendCancellableCoroutine { continuation ->
            val url = absoluteUrl(path)
            val request = Request.Builder().url(url).header("Accept", "application/json, text/plain, */*")
                .header("User-Agent", "jzmedia-android-tv/${BuildConfig.VERSION_NAME}")
            authHeadersFor(url).forEach { (key, value) -> request.header(key, value) }
            if (receiverToken != null) request.header("X-Receiver-Token", receiverToken)
            request.method(method, body?.toString()?.toRequestBody("application/json; charset=utf-8".toMediaType()))
            val call = (if (longRequest) playbackClient else client).newCall(request.build())
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (continuation.isActive) continuation.resumeWithException(ApiException(0,
                        if (e is InterruptedIOException) "服务器响应超时，请重试或检查网络"
                        else "无法连接服务器，请检查地址、网络或服务器状态"))
                }
                override fun onResponse(call: Call, response: Response) {
                    try {
                        val bytes = response.use {
                            if (!it.isSuccessful) {
                                val raw = it.body?.source()?.let { source ->
                                    source.request(8193)
                                    if (source.buffer.size <= 8192) source.readUtf8() else ""
                                }.orEmpty()
                                val code = runCatching {
                                    JSONObject(raw).let { error -> error.text("code").ifBlank { error.optJSONObject("detail")?.text("code").orEmpty() } }
                                }.getOrDefault("")
                                if (code == "viewing_locked") locked.tryEmit(Unit)
                                throw ApiException(it.code, viewingErrorMessage(code) ?: when (it.code) {
                                401, 403 -> "访问被拒绝，请检查连接中的访问令牌"
                                404 -> "内容不存在，或服务器尚未支持此功能"
                                409 -> "媒体状态已变化，请返回刷新后重试"
                                429 -> "服务器任务较多，请稍后重试"
                                503 -> "服务器或媒体暂不可用，请检查连接后重试"
                                in 300..399 -> "服务器发生重定向，请在连接中填写最终地址"
                                else -> "请求失败（${it.code}），请稍后重试"
                                }, code)
                            }
                            val source = it.body?.source() ?: throw ApiException(0, "服务器返回了空响应")
                            if (source.request(maxBytes.toLong() + 1)) throw ApiException(0, "服务器响应过大")
                            source.readByteArray()
                        }
                        if (continuation.isActive) continuation.resume(bytes)
                    } catch (e: Exception) {
                        if (continuation.isActive) continuation.resumeWithException(
                            if (e is ApiException) e else ApiException(0, "读取服务器响应失败，请重试"),
                        )
                    }
                }
            })
        }

    suspend fun checkConnection(): JSONObject {
        val info = get("/api/stream/client-info")
        if (info.optInt("protocol_version") != 1) throw ApiException(0, "服务器版本不兼容，请更新 jzmedia 服务端")
        val check = post("/api/stream/client-check", JSONObject().put("client", "android_tv").put("protocol_version", 1))
        if (check.optInt("protocol_version") != 1) throw ApiException(0, "服务器版本不兼容，请更新 jzmedia 服务端")
        return info
    }

    companion object {
        fun normalizeServerUrl(input: String): String {
            val text = input.trim()
            require(!text.contains('\\')) { "服务器地址格式无效" }
            val url = text.toHttpUrlOrNull() ?: throw IllegalArgumentException("请输入完整地址，例如 http://192.168.1.10:8080")
            require(url.username.isEmpty() && url.password.isEmpty() && url.query == null && url.fragment == null) {
                "服务器地址不能包含用户名、密码、查询参数或片段"
            }
            return url.newBuilder().encodedPath(url.encodedPath.trimEnd('/') + "/").build().toString()
        }
    }
}
