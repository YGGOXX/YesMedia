package com.webgrab.app.douyin

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

/**
 * 通道 A：原生 HTTP 解析（优先，不启动 WebView）。
 *
 * 只做公开页面的正常请求；遇到验证/登录不绕过，返回明确错误码，
 * 由上层决定是否打开页面让用户手动完成。
 */
object DouyinNativeResolver {

    const val REFERER = "https://www.douyin.com/"

    /**
     * 同步解析，返回统一 JSON（绝不抛异常）。
     * 输出同时包含新 schema（items/media_type）与旧字段 video_url，便于渐进接入。
     */
    @JvmStatic
    @JvmOverloads
    fun resolve(pageUrl: String, timeoutMs: Int = 20_000): String {
        val budget = timeoutMs.coerceIn(3_000, 60_000)
        val http = DouyinHttpClient(
            connectTimeoutMs = minOf(5_000, budget),
            readTimeoutMs = minOf(8_000, budget),
        )
        val deadline = System.currentTimeMillis() + budget
        return try {
            val target = DouyinLink.resolve(pageUrl, http)
            val html = http.fetchSharePage(target, deadline)
            val routerJson = RouterDataParser.extractRouterJson(html)
                ?: return errorJson(
                    DouyinErrorCode.PAGE_STRUCTURE_CHANGED,
                    "页面结构变了：没有找到作品数据",
                )
            if (looksLikeVerification(html) &&
                !routerJson.contains("aweme_id") &&
                !routerJson.contains("item_list")
            ) {
                return errorJson(
                    DouyinErrorCode.NEEDS_VERIFICATION,
                    "需要完成安全验证，请打开页面手动验证后重试",
                )
            }
            if (!routerJson.contains("aweme_id") && !routerJson.contains("item_list")) {
                return errorJson(
                    DouyinErrorCode.PAGE_STRUCTURE_CHANGED,
                    "页面结构变了：作品字段缺失",
                )
            }
            val media = RouterDataParser.parse(html, target.awemeId)
                ?: return errorJson(
                    if (target.awemeId.isNotBlank()) DouyinErrorCode.UNAVAILABLE
                    else DouyinErrorCode.PAGE_STRUCTURE_CHANGED,
                    if (target.awemeId.isNotBlank()) "没找到这个作品，可能已删除或不可见"
                    else "页面结构变了：作品数据解析不了",
                )
            if (media.awemeId.isNotBlank() && target.awemeId.isNotBlank() &&
                media.awemeId != target.awemeId
            ) {
                return errorJson(DouyinErrorCode.UNAVAILABLE, "没找到这个作品，可能已删除或不可见")
            }
            // 卡页/下载引导页会带一段宣传视频，不能当成作品直链。
            if (media.playUrl.contains("douyin-pc-web") && media.images.isEmpty()) {
                return errorJson(DouyinErrorCode.UNAVAILABLE, "没找到这个作品，可能已删除或不可见")
            }
            if (media.playUrl.isBlank() && media.images.isEmpty()) {
                return errorJson(
                    DouyinErrorCode.MEDIA_UNSUPPORTED,
                    "这个作品没有可下载的视频或图片",
                )
            }
            successJson(target, media)
        } catch (e: DouyinException) {
            errorJson(e.code, e.message)
        } catch (e: SocketTimeoutException) {
            errorJson(DouyinErrorCode.DETAIL_TIMEOUT, "连接抖音超时，请重试")
        } catch (e: UnknownHostException) {
            errorJson(DouyinErrorCode.NETWORK, "网络连不上，请检查网络后重试")
        } catch (e: ConnectException) {
            errorJson(DouyinErrorCode.NETWORK, "网络连不上，请检查网络后重试")
        } catch (e: IOException) {
            errorJson(DouyinErrorCode.NETWORK, "网络连接异常，请检查网络后重试")
        } catch (e: Exception) {
            errorJson(DouyinErrorCode.PAGE_LOAD_FAILED, "页面解析失败，请重试")
        }
    }

    private fun successJson(target: DouyinTarget, media: DouyinMedia): String {
        val root = JSONObject()
        root.put("ok", true)
        root.put("platform", "douyin")
        root.put("source", "native")
        root.put("resolved_url", target.pageUrl)
        root.put("aweme_id", media.awemeId.ifBlank { target.awemeId })
        root.put("title", media.title)
        root.put("author", media.author)
        root.put("cover", media.cover)
        root.put("referer", REFERER)

        val items = JSONArray()
        if (media.images.isNotEmpty()) {
            root.put("media_type", "gallery")
            media.images.forEachIndexed { index, image ->                val item = JSONObject()
                item.put("kind", "image")
                item.put("url", image.url)
                item.put("thumb", image.url)
                item.put("index", index)
                if (image.width > 0) item.put("width", image.width)
                if (image.height > 0) item.put("height", image.height)
                if (image.livePhotoUrl.isNotBlank()) {
                    val live = JSONObject()
                    live.put("url", image.livePhotoUrl)
                    item.put("live_photo", live)
                }
                items.put(item)
            }
        }
        if (media.images.isEmpty() && media.playUrl.isNotBlank()) {
            root.put("media_type", "video")
            val video = JSONObject()
            video.put("kind", "video")
            video.put("url", media.playUrl)
            if (media.playWmUrl.isNotBlank()) {
                video.put("fallback_url", media.playWmUrl)
            }
            if (media.width > 0) video.put("width", media.width)
            if (media.height > 0) video.put("height", media.height)
            if (media.size > 0) video.put("size", media.size)
            if (media.durationMs > 0) video.put("duration_ms", media.durationMs)
            video.put("watermark", media.playUrl.contains("playwm"))
            val ordered = JSONArray()
            ordered.put(video)
            root.put("items", ordered)
            root.put("video_url", media.playUrl) // 兼容现有 Python 桥
        } else {
            root.put("items", items)
            root.put("media_type", if (media.images.isNotEmpty()) "gallery" else "video")
        }

        root.put("error_code", JSONObject.NULL)
        root.put("error", JSONObject.NULL)
        return root.toString()
    }

    private fun errorJson(code: String, message: String): String {
        val root = JSONObject()
        root.put("ok", false)
        root.put("platform", "douyin")
        root.put("error_code", code)
        root.put("error", message)
        return root.toString()
    }

    private fun looksLikeVerification(html: String): Boolean {
        if (html.isEmpty()) return false
        val lowered = html.lowercase()
        return lowered.contains("captcha") ||
            lowered.contains("secsdk-captcha") ||
            lowered.contains("verify-bar") ||
            html.contains("安全验证")
    }
}
