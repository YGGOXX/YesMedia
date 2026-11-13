package com.webgrab.app.douyin

import java.net.URI

/** 抖音作品定位结果。 */
data class DouyinTarget(
    val awemeId: String,
    val kind: String, // "video" | "note"
    val pageUrl: String,
)

/**
 * 链接识别、短链跟随与作品 id 提取。
 * 纯本地：不访问任何第三方解析服务，只跟抖音自己的 302。
 */
object DouyinLink {

    private val URL_RE = Regex("""https?://[^\s"'<>，。；！？）】]+""")
    private val DIRECT_RE =
        Regex("""https?://(?:www\.|m\.)?douyin\.com/(video|note|slides)/(\d{8,})""")
    private val SHORT_RE = Regex("""https?://v\.douyin\.com/[A-Za-z0-9_-]+/?""")
    private val AWEME_QUERY_RE = Regex("""[?&]aweme_id=(\d{8,})""")
    private val PURE_ID_RE = Regex("""^\d{15,25}$""")

    private val ALLOWED_HOSTS = setOf(
        "douyin.com",
        "www.douyin.com",
        "m.douyin.com",
        "v.douyin.com",
        "iesdouyin.com",
        "www.iesdouyin.com",
    )

    /** 从分享文案里取第一个 URL，取不到返回 null。 */
    fun findUrl(text: String): String? = URL_RE.find(text)?.value?.trimEnd('.', ',', ';')

    /** 纯文本/纯 id 的快速判断，不联网。 */
    fun extractLocal(text: String): DouyinTarget? {
        val trimmed = text.trim()
        val raw = findUrl(trimmed)
        if (raw == null) {
            if (PURE_ID_RE.matches(trimmed)) {
                return DouyinTarget(trimmed, "video", "https://www.douyin.com/video/$trimmed")
            }
            return null
        }
        val direct = parseDirect(raw)
        if (direct != null) return direct
        if (SHORT_RE.matches(raw)) {
            return DouyinTarget("", "short", raw)
        }
        val queryId = AWEME_QUERY_RE.find(raw)?.groupValues?.get(1)
        if (queryId != null) return DouyinTarget(queryId, "video", "https://www.douyin.com/video/$queryId")
        return null
    }

    /**
     * 完整解析：本地识别 + 必要时跟随短链。
     * 失败抛 [DouyinException]，code 为 NO_LINK / SHORT_LINK_* / UNSUPPORTED_HOST。
     */
    fun resolve(text: String, http: DouyinHttpClient): DouyinTarget {
        val local = extractLocal(text)
            ?: throw DouyinException(DouyinErrorCode.NO_LINK, "没有找到抖音链接")
        if (local.kind != "short") return local

        var current = local.pageUrl
        repeat(3) {
            val location = try {
                http.redirectLocation(current)
            } catch (e: Exception) {
                throw DouyinException(DouyinErrorCode.SHORT_LINK_FAILED, "短链打不开，请重试", e)
            } ?: throw DouyinException(
                DouyinErrorCode.SHORT_LINK_UNRESOLVED,
                "短链没有跳到作品页，可能已过期，请重新复制分享链接",
            )
            val absolute = absolutize(current, location)
            parseDirect(absolute)?.let { return it }
            if (!isAllowedHost(absolute)) {
                throw DouyinException(DouyinErrorCode.SHORT_LINK_UNRESOLVED, "短链跳到了站外，请重新复制")
            }
            // 跳到首页/活动页：视为过期，不再盲目请求。
            if (isHomePage(absolute)) {
                throw DouyinException(
                    DouyinErrorCode.SHORT_LINK_UNRESOLVED,
                    "短链已失效（跳到了首页），请重新复制分享链接",
                )
            }
            current = absolute
        }
        throw DouyinException(DouyinErrorCode.SHORT_LINK_UNRESOLVED, "短链跳转次数过多，请重试")
    }

    /** 直接解析作品页 URL；不是作品页返回 null。 */
    fun parseDirect(raw: String): DouyinTarget? {
        val match = DIRECT_RE.find(raw) ?: return null
        val rawKind = match.groupValues[1]
        val id = match.groupValues[2]
        val kind = if (rawKind == "video") "video" else "note"
        val canonical = if (kind == "video") {
            "https://www.douyin.com/video/$id"
        } else {
            "https://www.douyin.com/note/$id"
        }
        return DouyinTarget(id, kind, canonical)
    }

    fun isAllowedHost(url: String): Boolean {
        val host = try {
            URI(url).host?.lowercase()
        } catch (_: Exception) {
            null
        } ?: return false
        return ALLOWED_HOSTS.contains(host) || host.endsWith(".douyin.com")
    }

    private fun isHomePage(url: String): Boolean {
        val path = try {
            URI(url).path.orEmpty()
        } catch (_: Exception) {
            return false
        }
        return path.isEmpty() || path == "/"
    }

    private fun absolutize(base: String, location: String): String {
        return try {
            URI(base).resolve(location).toString()
        } catch (_: Exception) {
            location
        }
    }
}
