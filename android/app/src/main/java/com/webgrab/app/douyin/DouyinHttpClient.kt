package com.webgrab.app.douyin

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.concurrent.ConcurrentHashMap

data class HttpResult(val code: Int, val body: String, val location: String?)

/**
 * 抖音页面请求。只用 HttpURLConnection + 自管 Cookie（ttwid），
 * 不引入 OkHttp，也不把 Cookie 交给 WebView。
 */
class DouyinHttpClient(
    private val connectTimeoutMs: Int = 8_000,
    private val readTimeoutMs: Int = 12_000,
) {

    companion object {
        const val IPHONE_UA =
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) " +
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 " +
                "Mobile/15E148 Safari/604.1"
        const val REFERER = "https://www.douyin.com/"
        private const val MAX_BODY = 8 * 1024 * 1024
    }

    // 分享页只需要 ttwid；按 name 存，发送给抖音域即可。
    private val cookies = ConcurrentHashMap<String, String>()

    fun fetchSharePage(target: DouyinTarget, deadlineAt: Long = Long.MAX_VALUE): String {
        val segment = if (target.kind == "note") "note" else "video"
        val url = "https://www.iesdouyin.com/share/$segment/${target.awemeId}/"
        var body = ""
        // 第一次只负责拿 Set-Cookie: ttwid；第二次带 cookie 才可能出 play_addr。
        repeat(4) { attempt ->
            val remaining = remainingMs(deadlineAt)
            if (remaining <= 0) {
                throw DouyinException(DouyinErrorCode.DETAIL_TIMEOUT, "解析超时，请重试")
            }
            val result = get(url, readTimeoutMs = minOf(readTimeoutMs, remaining.toInt()))
            if (result.code == 404 || result.code == 403 || result.code == 410) {
                throw DouyinException(
                    DouyinErrorCode.UNAVAILABLE,
                    "作品不存在、已删除或不可访问",
                )
            }
            if (result.code >= 500 || result.code == 429) {
                throw DouyinException(
                    DouyinErrorCode.PAGE_LOAD_FAILED,
                    "抖音页面打不开（HTTP ${result.code}），请稍后重试",
                )
            }
            if (result.body.isNotEmpty()) body = result.body
            if (looksLikeMedia(body)) return body
            if (attempt < 3) Thread.sleep(700)
        }
        if (body.isEmpty()) {
            throw DouyinException(DouyinErrorCode.PAGE_LOAD_FAILED, "抖音页面没有返回内容")
        }
        return body
    }

    private fun remainingMs(deadlineAt: Long): Long {
        if (deadlineAt == Long.MAX_VALUE) return Long.MAX_VALUE
        return deadlineAt - System.currentTimeMillis()
    }

    fun get(
        url: String,
        ua: String = IPHONE_UA,
        referer: String = REFERER,
        follow: Boolean = true,
        readTimeoutMs: Int = this.readTimeoutMs,
    ): HttpResult {
        val conn = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = "GET"
            instanceFollowRedirects = follow
            connectTimeout = connectTimeoutMs
            readTimeout = readTimeoutMs
            setRequestProperty("User-Agent", ua)
            setRequestProperty("Referer", referer)
            setRequestProperty(
                "Accept",
                "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            )
            setRequestProperty("Accept-Language", "zh-CN,zh;q=0.9")
            cookieHeader()?.let { setRequestProperty("Cookie", it) }
        }
        try {
            val code = conn.responseCode
            storeCookies(conn.headerFields["Set-Cookie"].orEmpty())
            val location = conn.getHeaderField("Location")
            val body = if (code in 200..299) readBody(conn) else ""
            return HttpResult(code, body, location)
        } finally {
            conn.disconnect()
        }
    }

    /** 短链：不跟随，只读 302 的 Location。 */
    fun redirectLocation(url: String): String? {
        val result = get(url, follow = false)
        if (result.code in 300..399 && !result.location.isNullOrBlank()) return result.location
        return null
    }

    private fun cookieHeader(): String? {
        if (cookies.isEmpty()) return null
        return cookies.entries.joinToString("; ") { "${it.key}=${it.value}" }
    }

    private fun storeCookies(headers: List<String>) {
        for (header in headers) {
            val pair = header.substringBefore(';').trim()
            val eq = pair.indexOf('=')
            if (eq <= 0) continue
            val name = pair.substring(0, eq).trim()
            val value = pair.substring(eq + 1).trim()
            if (name.isEmpty()) continue
            if (value.isEmpty() || value.equals("deleted", ignoreCase = true)) {
                cookies.remove(name)
            } else {
                cookies[name] = value
            }
        }
    }

    private fun readBody(conn: HttpURLConnection): String {
        conn.inputStream.use { input ->
            val out = ByteArrayOutputStream()
            val buffer = ByteArray(16 * 1024)
            var total = 0
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                total += n
                if (total > MAX_BODY) throw IOException("页面过大")
                out.write(buffer, 0, n)
            }
            return out.toString(Charsets.UTF_8.name())
        }
    }

    private fun looksLikeMedia(body: String): Boolean {
        if (body.isEmpty()) return false
        return body.contains("play_addr") ||
            body.contains("playAddr") ||
            body.contains("\"images\"") ||
            body.contains("item_list")
    }
}

/** 校验 host 是否属于抖音（避免短链跳到站外后继续请求）。 */
internal fun uriHost(url: String): String? = try {
    URI(url).host?.lowercase()
} catch (_: Exception) {
    null
}
