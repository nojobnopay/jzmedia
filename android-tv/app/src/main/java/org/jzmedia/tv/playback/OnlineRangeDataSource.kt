package org.jzmedia.tv.playback

import android.net.Uri
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.TransferListener
import androidx.media3.datasource.okhttp.OkHttpDataSource
import okhttp3.Call
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import org.jzmedia.tv.data.JzApi
import java.io.EOFException
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.TimeUnit

internal const val ONLINE_RANGE_BYTES = 8L * 1024 * 1024
internal class OnlineMediaResponseException(val status: Int) : IOException("在线媒体请求失败（$status）")

/** Media credentials are attached only after checking the configured server and base path. */
internal fun onlineMediaHttpClient(api: JzApi): OkHttpClient = OkHttpClient.Builder()
    .followRedirects(false).followSslRedirects(false)
    .connectTimeout(10, TimeUnit.SECONDS).readTimeout(35, TimeUnit.SECONDS)
    .callTimeout(45, TimeUnit.SECONDS)
    .addInterceptor { chain ->
        val url = chain.request().url.toString()
        try { api.absoluteUrl(url) }
        catch (error: IllegalArgumentException) { throw IOException("在线地址不属于当前服务器", error) }
        val request = chain.request().newBuilder()
        api.authHeadersFor(url).forEach { (name, value) -> request.header(name, value) }
        val response = chain.proceed(request.build())
        if (response.isRedirect) {
            response.close()
            throw IOException("在线地址发生重定向，请检查服务器地址")
        }
        response
    }.build()

/**
 * A progressive extractor expects one logical stream. The gateway deliberately
 * limits each MP4 response to 8 MiB; treat its Content-Range total as the stream
 * length and open the next bounded range rather than presenting a false EOF.
 * This reader has no Android dependency, allowing real HTTP range regressions.
 */
internal class OnlineRangeReader(private val client: OkHttpClient, private val url: String,
                                 private val chunkBytes: Long = ONLINE_RANGE_BYTES) {
    init { require(chunkBytes in 1..ONLINE_RANGE_BYTES) }
    @Volatile private var closed = false
    @Volatile private var call: Call? = null
    @Volatile private var response: Response? = null
    private val callLock = Any()
    private var started = false
    private var ioActive = false
    private var body: InputStream? = null
    private var position = 0L
    private var remaining = 0L
    private var chunkRemaining = 0L
    private var total: Long? = null
    private var validator: Pair<String, String>? = null
    var responseHeaders: Map<String, List<String>> = emptyMap()
        private set

    fun open(start: Long, length: Long = -1L): Long {
        require(start >= 0 && (length == -1L || length > 0))
        synchronized(callLock) {
            if (closed || started) throw IOException("在线播放已停止")
            started = true; ioActive = true
        }
        try {
            position = start; remaining = length; total = null; validator = null
            nextChunk()
            val available = ((total ?: start) - start).coerceAtLeast(0)
            remaining = if (length < 0) available else minOf(length, available)
            return remaining
        } finally { finishIo() }
    }

    private fun nextChunk() {
        if (closed) throw IOException("在线播放已停止")
        response?.close(); response = null; body = null
        val size = minOf(chunkBytes, if (remaining > 0) remaining else chunkBytes)
        val end = if (position > Long.MAX_VALUE - size) Long.MAX_VALUE else position + size - 1
        val request = Request.Builder().url(url).header("Accept-Encoding", "identity")
            .header("Range", "bytes=$position-$end")
        validator?.let { request.header("If-Range", it.second) }
        val active = client.newCall(request.build())
        val registered = synchronized(callLock) {
            if (closed) false else { call = active; true }
        }
        if (!registered) { active.cancel(); throw IOException("在线播放已停止") }
        val result = active.execute()
        response = result
        if (closed) { result.close(); throw IOException("在线播放已停止") }
        responseHeaders = result.headers.toMultimap()
        if (result.code == 416) {
            val endOfFile = Regex("bytes \\*/([0-9]+)").matchEntire(result.header("Content-Range").orEmpty())
                ?.groupValues?.get(1)?.toLongOrNull()
            if (endOfFile != null && position == endOfFile && (total == null || total == endOfFile)) {
                total = endOfFile; chunkRemaining = 0; result.close(); response = null; return
            }
        }
        if (result.code != 206) { result.close(); response = null; throw OnlineMediaResponseException(result.code) }
        val match = Regex("bytes ([0-9]+)-([0-9]+)/([0-9]+)").matchEntire(result.header("Content-Range").orEmpty())
        val numbers = match?.groupValues?.drop(1)?.map { it.toLongOrNull() }
        val first = numbers?.get(0); val last = numbers?.get(1); val count = numbers?.get(2)
        if (first == null || last == null || count == null || first != position || first > last || last >= count
            || last != minOf(end, count - 1) || (total != null && total != count)
            || validator?.let { result.header(it.first) != it.second } == true
            || result.body?.contentLength() != last - first + 1
            || result.header("Content-Encoding")?.lowercase()?.let { it != "identity" } == true) {
            result.close(); response = null; throw IOException("在线媒体区间响应不一致，请重新播放")
        }
        if (validator == null) {
            val etag = result.header("ETag")?.takeIf { !it.startsWith("W/") && it.length <= 512 }
            val modified = result.header("Last-Modified")?.takeIf { it.length <= 512 }
            validator = when { etag != null -> "ETag" to etag; modified != null -> "Last-Modified" to modified; else -> null }
        }
        total = count; chunkRemaining = last - first + 1
        body = result.body!!.byteStream()
    }

    fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        synchronized(callLock) {
            if (closed || !started) throw IOException("在线播放已停止")
            check(!ioActive) { "An online range has only one reading worker" }
            ioActive = true
        }
        try {
            if (remaining == 0L) return -1
            if (chunkRemaining == 0L) nextChunk()
            val count = body?.read(buffer, offset, minOf(length.toLong(), remaining, chunkRemaining).toInt()) ?: -1
            if (closed) throw IOException("在线播放已停止")
            if (count < 0) throw EOFException("在线媒体响应提前结束")
            position += count; remaining -= count; chunkRemaining -= count
            return count
        } finally { finishIo() }
    }

    /** Caller holds callLock, and no worker is inside open/read. Network close stays outside the lock. */
    private fun detachResponse(): Response? {
        val previous = response
        response = null; body = null; chunkRemaining = 0; remaining = 0
        return previous
    }

    private fun finishIo() {
        val previous = synchronized(callLock) {
            ioActive = false
            if (closed) detachResponse() else null
        }
        previous?.close()
    }

    fun close() {
        val (active, previous) = synchronized(callLock) {
            closed = true
            val active = call.also { call = null }
            active to if (ioActive) null else detachResponse()
        }
        // Call cancellation can interrupt a read. Closing its Okio body from a
        // different thread cannot: the reading worker owns that close in finally.
        active?.cancel()
        previous?.close()
    }
}

/** Resolve only this session's rewritten gateway paths, preserving a reverse-proxy prefix. */
internal fun onlineSessionMediaUrl(api: JzApi, sessionId: String, value: String): String {
    if (!Regex("[A-Za-z0-9_-]{32,128}").matches(sessionId) || value.startsWith("//") || value.contains('\\'))
        throw IOException("在线媒体身份无效")
    val base = api.baseUrl.toHttpUrlOrNull() ?: throw IOException("在线服务器地址无效")
    val target = if (value.startsWith('/')) base.resolve(value) else value.toHttpUrlOrNull()
    if (target == null || target.scheme != base.scheme || target.host != base.host || target.port != base.port
        || target.username.isNotEmpty() || target.password.isNotEmpty() || target.query != null || target.fragment != null)
        throw IOException("在线地址不属于当前服务器")
    val root = "/api/online/sessions/$sessionId/"
    val prefixed = api.absoluteUrl(root).toHttpUrlOrNull()!!.encodedPath
    val suffix = when {
        target.encodedPath.startsWith(prefixed) -> target.encodedPath.removePrefix(prefixed)
        target.encodedPath.startsWith(root) -> target.encodedPath.removePrefix(root)
        else -> throw IOException("在线媒体身份已变化")
    }
    if (suffix != "media" && !Regex("resources/[A-Za-z0-9_-]{32,128}").matches(suffix))
        throw IOException("在线媒体资源地址无效")
    return api.absoluteUrl(root + suffix)
}

@UnstableApi
private class OnlineSessionDataSource(private val api: JzApi, private val sessionId: String,
                                      private val delegate: DataSource) : DataSource {
    override fun addTransferListener(transferListener: TransferListener) = delegate.addTransferListener(transferListener)
    override fun open(dataSpec: DataSpec): Long = delegate.open(dataSpec.buildUpon()
        .setUri(Uri.parse(onlineSessionMediaUrl(api, sessionId, dataSpec.uri.toString()))).build())
    override fun read(buffer: ByteArray, offset: Int, length: Int): Int = delegate.read(buffer, offset, length)
    override fun getUri(): Uri? = delegate.uri
    override fun getResponseHeaders(): Map<String, List<String>> = delegate.responseHeaders
    override fun close() = delegate.close()
}

@UnstableApi
internal class OnlineRangeDataSource(private val api: JzApi, private val http: OkHttpClient,
                                     private val expectedUrl: String) : BaseDataSource(true) {
    private var reader: OnlineRangeReader? = null
    private var opened = false
    private var uri: Uri? = null

    override fun open(dataSpec: DataSpec): Long {
        check(reader == null && !opened)
        require(dataSpec.httpMethod == DataSpec.HTTP_METHOD_GET && dataSpec.httpBody == null)
        val url = try { api.absoluteUrl(dataSpec.uri.toString()) }
        catch (error: IllegalArgumentException) { throw IOException("在线地址不属于当前服务器", error) }
        if (url != expectedUrl) throw IOException("在线媒体身份已变化")
        transferInitializing(dataSpec)
        val next = OnlineRangeReader(http, url)
        reader = next; uri = dataSpec.uri
        try {
            val length = next.open(dataSpec.position, dataSpec.length)
            opened = true; transferStarted(dataSpec)
            return length
        } catch (error: Exception) { next.close(); reader = null; uri = null; throw error }
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        val count = reader?.read(buffer, offset, length) ?: C.RESULT_END_OF_INPUT
        if (count > 0) bytesTransferred(count)
        return count
    }
    override fun getUri(): Uri? = uri
    override fun getResponseHeaders(): Map<String, List<String>> = reader?.responseHeaders ?: emptyMap()
    override fun close() {
        try { reader?.close() }
        finally { reader = null; uri = null; if (opened) { opened = false; transferEnded() } }
    }
}

@UnstableApi
internal class OnlineMediaDataSourceFactory(private val api: JzApi, private val http: OkHttpClient,
                                            private val transport: String, private val mediaUrl: String) : DataSource.Factory {
    private val sessionId = Regex("/api/online/sessions/([A-Za-z0-9_-]{32,128})/media").matchEntire(mediaUrl)
        ?.groupValues?.get(1) ?: throw IOException("在线媒体身份无效")
    override fun createDataSource(): DataSource = if (transport == "mp4")
        OnlineRangeDataSource(api, http, api.absoluteUrl(mediaUrl))
    else OnlineSessionDataSource(api, sessionId, OkHttpDataSource.Factory(http).createDataSource())
}
