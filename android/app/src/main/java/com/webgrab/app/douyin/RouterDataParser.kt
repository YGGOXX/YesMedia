package com.webgrab.app.douyin

import org.json.JSONArray
import org.json.JSONObject

data class DouyinImage(
    val url: String,
    val width: Int,
    val height: Int,
    val livePhotoUrl: String = "",
)

data class DouyinMedia(
    val awemeId: String,
    val title: String,
    val author: String,
    val cover: String,
    val durationMs: Long,
    val width: Int,
    val height: Int,
    val size: Long,
    val playUrl: String,
    val playWmUrl: String,
    val images: List<DouyinImage>,
    val musicUrl: String,
)

/**
 * 解析抖音分享页里的 `window._ROUTER_DATA`。
 *
 * 不依赖固定路径：分享页的 loaderData key 会变（video_(id)/page、note_(id)/page…），
 * 所以在整棵 JSON 里找第一个带 aweme_id 且带 video/images 的对象。
 */
object RouterDataParser {

    fun parse(html: String, expectedAwemeId: String = ""): DouyinMedia? {
        val json = extractRouterJson(html) ?: return null
        val root = try {
            JSONObject(json)
        } catch (_: Exception) {
            return null
        }
        val aweme = findAweme(root, expectedAwemeId) ?: return null
        return build(aweme)
    }

    /** 从 HTML 里截取 `window._ROUTER_DATA = {...}` 的 JSON。 */
    fun extractRouterJson(html: String): String? {
        val markers = listOf(
            "window._ROUTER_DATA",
            "window.__INITIAL_STATE__",
            "window._SSR_HYDRATED_DATA",
        )
        for (marker in markers) {
            var from = html.indexOf(marker)
            var guard = 0
            while (from >= 0 && guard < 5) {
                guard++
                val eq = html.indexOf('=', from + marker.length)
                if (eq < 0) break
                val brace = html.indexOf('{', eq + 1)
                if (brace < 0) break
                sliceBalanced(html, brace)?.let { if (it.length > 32) return it }
                from = html.indexOf(marker, from + marker.length)
            }
        }
        // 兜底：<script id="RENDER_DATA"> 里是 URL 编码的 JSON。
        val render = Regex("""<script[^>]*id=["']RENDER_DATA["'][^>]*>(.*?)</script>""", RegexOption.DOT_MATCHES_ALL)
            .find(html)?.groupValues?.get(1)
        if (!render.isNullOrBlank()) {
            val decoded = try {
                java.net.URLDecoder.decode(render.trim(), "UTF-8")
            } catch (_: Exception) {
                render
            }
            if (decoded.trimStart().startsWith("{")) return decoded
        }
        return null
    }

    private fun sliceBalanced(text: String, startBrace: Int): String? {
        var depth = 0
        var inString = false
        var escaped = false
        for (i in startBrace until text.length) {
            val c = text[i]
            if (inString) {
                when {
                    escaped -> escaped = false
                    c == '\\' -> escaped = true
                    c == '"' -> inString = false
                }
                continue
            }
            when (c) {
                '"' -> inString = true
                '{' -> depth++
                '}' -> {
                    depth--
                    if (depth == 0) return text.substring(startBrace, i + 1)
                }
            }
        }
        return null
    }

    private fun findAweme(root: JSONObject, expectedId: String = ""): JSONObject? {
        val queue = ArrayDeque<Any>()
        queue.add(root)
        var visited = 0
        var first: JSONObject? = null
        while (queue.isNotEmpty() && visited < 20_000) {
            visited++
            when (val node = queue.removeFirst()) {
                is JSONObject -> {
                    if (looksLikeAweme(node)) {
                        if (expectedId.isBlank()) return node
                        if (awemeIdOf(node) == expectedId) return node
                        if (first == null) first = node
                    }
                    for (key in node.keys()) {
                        val value = node.opt(key)
                        if (value is JSONObject || value is JSONArray) queue.add(value)
                    }
                }
                is JSONArray -> {
                    for (i in 0 until minOf(node.length(), 200)) {
                        val value = node.opt(i)
                        if (value is JSONObject || value is JSONArray) queue.add(value)
                    }
                }
            }
        }
        return if (expectedId.isBlank()) first else null
    }

    private fun awemeIdOf(obj: JSONObject): String =
        firstString(obj, "aweme_id", "awemeId", "itemId", "item_id").orEmpty()

    private fun looksLikeAweme(obj: JSONObject): Boolean {
        val id = firstString(obj, "aweme_id", "awemeId", "itemId", "item_id")
        if (id.isNullOrBlank()) return false
        return obj.opt("video") is JSONObject || obj.opt("images") is JSONArray
    }

    private fun build(aweme: JSONObject): DouyinMedia {
        val id = firstString(aweme, "aweme_id", "awemeId", "itemId", "item_id").orEmpty()
        val title = firstString(aweme, "desc", "title", "caption").orEmpty()
        val author = (aweme.optJSONObject("author")?.let {
            firstString(it, "nickname", "name", "unique_id")
        }) ?: firstString(aweme, "nickname", "author_name").orEmpty()

        val video = aweme.optJSONObject("video")
        val playAddr = video?.optJSONObject("play_addr")
            ?: video?.optJSONObject("playAddr")
            ?: video?.optJSONObject("play_addr_h264")
        var play = firstUrl(playAddr)
        if (play.isBlank() && video != null) {
            play = firstString(video, "playApi", "play_url", "download_url").orEmpty()
        }
        var wm = ""
        if (play.contains("/playwm/")) {
            wm = play
            play = play.replace("/playwm/", "/play/")
        }
        // 图集里的“视频”有时是幻灯片/引导资源，play 地址里塞了整条 URL，不能当作品视频。
        if (play.contains("video_id=http", ignoreCase = true) ||
            play.contains("douyin-pc-web", ignoreCase = true)
        ) {
            play = ""
            wm = ""
        }

        val cover = video?.optJSONObject("cover")?.let { firstUrl(it) }
            ?: video?.optJSONObject("origin_cover")?.let { firstUrl(it) }
            ?: video?.optJSONObject("dynamic_cover")?.let { firstUrl(it) }
            ?: ""

        val width = firstInt(playAddr, "width", "w")
            .takeIf { it > 0 } ?: firstInt(video, "width", "w").coerceAtLeast(0)
        val height = firstInt(playAddr, "height", "h")
            .takeIf { it > 0 } ?: firstInt(video, "height", "h").coerceAtLeast(0)
        val size = firstLong(playAddr, "data_size", "dataSize", "size")
            .takeIf { it > 0 } ?: firstLong(video, "data_size", "dataSize", "size")
        val duration = firstLong(video, "duration", "duration_ms")
            .takeIf { it > 0 } ?: firstLong(aweme, "duration", "duration_ms").coerceAtLeast(0)

        val images = mutableListOf<DouyinImage>()
        val imageArray = aweme.optJSONArray("images")
        if (imageArray != null) {
            for (i in 0 until minOf(imageArray.length(), 60)) {
                val img = imageArray.optJSONObject(i) ?: continue
                val url = firstUrl(img)
                if (url.isBlank()) continue
                val live = img.optJSONObject("video")?.let { videoOf(it) }.orEmpty()
                images.add(
                    DouyinImage(
                        url = url,
                        width = firstInt(img, "width", "w"),
                        height = firstInt(img, "height", "h"),
                        livePhotoUrl = live,
                    )
                )
            }
        }

        val music = aweme.optJSONObject("music")?.optJSONObject("play_url")?.let { firstUrl(it) }
            ?: aweme.optJSONObject("music")?.optJSONObject("playUrl")?.let { firstUrl(it) }
            ?: ""

        return DouyinMedia(
            awemeId = id,
            title = title.ifBlank { "抖音作品" },
            author = author,
            cover = cover,
            durationMs = duration,
            width = width,
            height = height,
            size = size,
            playUrl = unescape(play),
            playWmUrl = unescape(wm),
            images = images,
            musicUrl = unescape(music),
        )
    }

    private fun videoOf(video: JSONObject): String {
        val addr = video.optJSONObject("play_addr")
            ?: video.optJSONObject("playAddr")
        return firstUrl(addr)
    }

    private fun firstUrl(obj: JSONObject?): String {
        if (obj == null) return ""
        val list = obj.optJSONArray("url_list") ?: obj.optJSONArray("urlList")
        if (list != null && list.length() > 0) {
            for (i in 0 until list.length()) {
                val value = list.optString(i)
                if (value.startsWith("http")) return unescape(value)
            }
        }
        val direct = firstString(obj, "url", "playApi", "uri")
        if (!direct.isNullOrBlank() && direct.startsWith("http")) return unescape(direct)
        return ""
    }

    private fun firstString(obj: JSONObject?, vararg keys: String): String? {
        if (obj == null) return null
        for (key in keys) {
            if (!obj.has(key) || obj.isNull(key)) continue
            val value = obj.optString(key, "")
            if (value.isNotBlank() && !value.equals("null", true)) return value
        }
        return null
    }

    private fun firstInt(obj: JSONObject?, vararg keys: String): Int {
        if (obj == null) return 0
        for (key in keys) {
            val value = obj.optInt(key, 0)
            if (value > 0) return value
        }
        return 0
    }

    private fun firstLong(obj: JSONObject?, vararg keys: String): Long {
        if (obj == null) return 0L
        for (key in keys) {
            val value = obj.optLong(key, 0L)
            if (value > 0) return value
        }
        return 0L
    }

    private fun unescape(url: String): String {
        return url.replace("\\u002F", "/").replace("\\/", "/")
    }
}
