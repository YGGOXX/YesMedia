package com.webgrab.app.douyin

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.view.ViewGroup
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import com.webgrab.app.WebGrabApp
import org.json.JSONObject
import org.json.JSONTokener
import java.io.ByteArrayInputStream
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

/**
 * 用隐藏 WebView 解析抖音视频直链（无水印优先，带水印兜底）。
 *
 * 背景：抖音第三方解析接口全部失效，但真实播放页里的 <video>.currentSrc 仍是
 * douyinvod 的 mime_type=video_mp4 直链，无 Cookie、只要带 Referer 就能下载。
 *
 * 调用约定（Python/Chaquopy 侧通过 jclass 调用）：
 * ```
 * com.webgrab.app.douyin.DouyinResolver.resolve(pageUrl, timeoutMs)
 * ```
 * 返回 JSON（绝不抛异常）：
 * - 成功：{"ok":true,"video_url":"...","title":"...","cover":"...","error":"","referer":"https://www.douyin.com/","watermark":false}
 * - 失败：{"ok":false,"error":"中文原因"}
 *
 * 两轮策略：
 * 1. 桌面 Chrome UA 打开 https://www.douyin.com/video/<id>，注入 video.play()，立即轮询
 *    currentSrc / shouldInterceptRequest 抓 douyinvod 直链（实测 4s 内出结果）。
 * 2. 第一轮 8s 无结果 → 切移动 UA 打开 https://m.douyin.com/share/video/<id>，
 *    只读 video.src（playwm，无需播放即可读到），拿到也算成功，标 watermark=true。
 * 总预算 timeoutMs（默认 25s）。
 *
 * 其余要点：
 * - 任意线程可调；内部切主线程建 1x1 WebView（能挂到当前 Activity 就挂，挂不上就游离加载）。
 * - 移动 UA 进 m 站登录墙，所以第一轮必须桌面 UA。
 * - 无论成功失败/超时，最终都在主线程销毁 WebView。
 */
object DouyinResolver {

    private const val TAG = "DouyinResolver"

    // 桌面 Chrome UA：移动 UA 会跳 m 站并弹登录墙，桌面页直接出无水印直链。
    private const val DESKTOP_UA =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"

    // 移动 UA：只用于第二轮 m 站 share 页（拿 playwm 带水印兜底）。
    private const val MOBILE_UA =
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 " +
            "(KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36"

    private const val REFERER_WEB = "https://www.douyin.com/"
    private const val REFERER_MOBILE = "https://m.douyin.com/"

    private const val ROUND1_TIMEOUT_MS = 8_000L
    private const val FALLBACK_GRACE_MS = 1_500L
    private const val POLL_INTERVAL_MS = 700L
    private const val MAX_POLLS = 40

    /** 同一链接的隐藏 WebView 解析串行化：并发调用共享第一个任务的结果，避免重复建 WebView。 */
    private val legacyGate = LegacyParseGate()

    /**
     * 解析抖音视频页，返回 JSON 字符串。任意线程可调，不抛异常。
     *
     * @param pageUrl   形如 https://www.douyin.com/video/<id> 的页面地址
     * @param timeoutMs 总超时（默认 25s，超出 [3000,120000] 会夹到默认值）
     */
    @JvmStatic
    @JvmOverloads
    fun resolve(pageUrl: String, timeoutMs: Int = 25_000): String {
        val timeout = if (timeoutMs in 3_000..120_000) timeoutMs else 25_000
        val startedAt = SystemClock.elapsedRealtime()

        // 通道 A：原生 HTTP（快，不需要 WebView）。失败不抛，只记录错误码。
        val nativeBudget = minOf(8_000, maxOf(4_000, timeout / 3))
        val nativeJson = try {
            DouyinNativeResolver.resolve(pageUrl, nativeBudget)
        } catch (t: Throwable) {
            Log.e(TAG, "native resolver crashed", t)
            """{"ok":false,"error_code":"PAGE_LOAD_FAILED","error":"原生解析失败"}"""
        }
        if (isOkJson(nativeJson)) return nativeJson
        Log.i(TAG, "native failed: ${nativeJson.take(240)}")

        // 通道 B：保留原有 WebView 逻辑；同一链接并发调用只建一个 WebView。
        val elapsed = (SystemClock.elapsedRealtime() - startedAt).toInt()
        val remaining = (timeout - elapsed).coerceIn(8_000, timeout)
        val legacyJson = try {
            legacyGate.run(pageUrl.trim(), remaining.toLong() + 2_000L) {
                resolveBlocking(pageUrl, remaining)
            }
        } catch (t: Throwable) {
            Log.e(TAG, "resolve crashed", t)
            failure("解析失败：" + oneLine(t), "PAGE_LOAD_FAILED")
        }
        return mergeFailure(nativeJson, legacyJson)
    }

    private fun isOkJson(raw: String): Boolean = try {
        JSONObject(raw).optBoolean("ok")
    } catch (_: Exception) {
        false
    }

    /**
     * 合并“原生失败 + WebView 失败”的 JSON：保留原有 error 文案，补上错误码与原生原因。
     * 单测直接调用；不依赖 Android WebView。
     */
    internal fun mergeFailure(nativeJson: String, legacyJson: String): String {
        val legacy = try {
            JSONObject(legacyJson)
        } catch (_: Exception) {
            return legacyJson
        }
        if (legacy.optBoolean("ok")) return legacyJson
        val native = try {
            JSONObject(nativeJson)
        } catch (_: Exception) {
            null
        }
        val nativeCode = native?.optString("error_code").orEmpty()
        val nativeError = native?.optString("error").orEmpty()
        val message = legacy.optString("error").ifBlank {
            nativeError.ifBlank { "解析失败，请重试" }
        }
        val out = JSONObject()
        out.put("ok", false)
        out.put("error_code", nativeCode.ifBlank { "NO_MEDIA" })
        out.put("error", message)
        if (nativeCode.isNotBlank()) out.put("native_error_code", nativeCode)
        if (nativeError.isNotBlank()) out.put("native_error", nativeError)
        return out.toString()
    }

    // ------------------------------------------------------------------ 主流程

    private fun resolveBlocking(pageUrl: String, timeoutMs: Int): String {
        val url = pageUrl.trim()
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            return failure("链接格式不对，需要以 http(s):// 开头的视频页地址", "INVALID_INPUT")
        }
        if (Looper.myLooper() == Looper.getMainLooper()) {
            return failure("解析不能在主线程调用（会卡住界面），请放到后台线程", "PAGE_LOAD_FAILED")
        }
        val app: WebGrabApp = try {
            WebGrabApp.instance
        } catch (t: Throwable) {
            return failure("应用还没初始化，无法解析", "PAGE_LOAD_FAILED")
        }

        val main = Handler(Looper.getMainLooper())
        val latch = CountDownLatch(1)
        val done = AtomicBoolean(false)
        val video = AtomicReference<String?>(null)
        val fallback = AtomicReference<String?>(null)
        val title = AtomicReference("")
        val cover = AtomicReference("")
        val error = AtomicReference<String?>(null)
        val errorCode = AtomicReference<String?>(null)
        val webViewRef = AtomicReference<WebView?>(null)
        val containerRef = AtomicReference<ViewGroup?>(null)
        val graceScheduled = AtomicBoolean(false)
        val polling = AtomicBoolean(false)
        val pollGen = AtomicInteger(0)
        val round2 = AtomicBoolean(false)
        var polls = 0

        fun finish(src: String?, errMsg: String?, code: String = "") {
            if (!done.compareAndSet(false, true)) return
            if (!src.isNullOrBlank()) video.set(src)
            if (!errMsg.isNullOrBlank()) error.set(errMsg)
            if (code.isNotBlank()) errorCode.set(code)
            latch.countDown()
        }

        fun bail(msg: String, code: String = "") = finish(null, msg, code)

        fun scheduleFallback() {
            if (!graceScheduled.compareAndSet(false, true)) return
            main.postDelayed({
                if (!done.get()) {
                    val fb = fallback.get()
                    if (fb.isNullOrBlank()) {
                        bail("页面没有返回可用的视频地址，可能视频需要登录、已删除或网络太慢")
                    } else {
                        Log.i(TAG, "use watermarked fallback")
                        finish(fb, null)
                    }
                }
            }, FALLBACK_GRACE_MS)
        }

        /** 收到一个候选地址：无水印立即完成；第二轮/带水印视情况直接完成或先兜底等一会儿。 */
        fun acceptVideo(raw: String, pageTitle: String, pageCover: String) {
            if (raw.isBlank()) return
            val src = normalizeUrl(raw)
            if (src.isBlank()) return
            // 页面上的宣传片/封面动图也可能是 <video>，只接受抖音播放 CDN。
            if (!isPlayableCandidate(src)) {
                Log.i(TAG, "ignore non-playable candidate: ${src.take(160)}")
                return
            }
            if (pageTitle.isNotBlank()) title.set(pageTitle)
            if (pageCover.isNotBlank()) cover.set(pageCover)
            if (done.get()) return
            if (isPreferredVideo(src)) {
                Log.i(TAG, "got clean video url: ${src.take(160)}")
                finish(src, null)
            } else if (round2.get()) {
                // 第二轮只求拿到 playwm（带水印），不再等待
                Log.i(TAG, "got watermarked url (round 2)")
                finish(src, null)
            } else {
                fallback.compareAndSet(null, src)
                scheduleFallback()
            }
        }

        /** 读页面可见文字，识别登录墙 / 验证码 / 访问限制；只提示，不绕过。 */
        fun checkPageState(genValue: Int) {
            if (done.get() || genValue != pollGen.get()) return
            val wv = webViewRef.get() ?: return
            try {
                wv.evaluateJavascript(PAGE_STATE_JS) { raw ->
                    if (done.get() || genValue != pollGen.get()) return@evaluateJavascript
                    val (href, text) = parsePageState(raw) ?: return@evaluateJavascript
                    val state = DouyinPageStateDetector.detect(href.ifBlank { url }, text)
                    if (state.kind != DouyinPageKind.OK) {
                        Log.i(TAG, "page state detected: ${state.kind}")
                        finish(null, state.message, state.code)
                    }
                }
            } catch (t: Throwable) {
                Log.w(TAG, "page state check failed", t)
            }
        }

        fun newPoll(genValue: Int): Runnable = object : Runnable {
            override fun run() {
                if (done.get() || genValue != pollGen.get()) {
                    polling.set(false)
                    return
                }
                val wv = webViewRef.get() ?: return
                try {
                    wv.evaluateJavascript(EXTRACT_JS) { raw ->
                        if (genValue != pollGen.get()) return@evaluateJavascript
                        parseExtraction(raw)?.let { (src, t, c) -> acceptVideo(src, t, c) }
                    }
                    // 页面状态（登录墙/验证码/访问限制）只在还没拿到视频时检查。
                    if (polls % 4 == 3) checkPageState(genValue)
                } catch (t: Throwable) {
                    Log.w(TAG, "evaluateJavascript failed", t)
                }
                polls++
                if (!done.get() && polls < MAX_POLLS && genValue == pollGen.get()) {
                    main.postDelayed(this, POLL_INTERVAL_MS)
                } else {
                    polling.set(false)
                }
            }
        }

        fun startPolling() {
            if (done.get()) return
            if (polling.compareAndSet(false, true)) main.post(newPoll(pollGen.get()))
        }

        fun buildClient(finalRound: Boolean): WebViewClient = object : WebViewClient() {

            override fun shouldInterceptRequest(
                view: WebView,
                request: WebResourceRequest
            ): WebResourceResponse? {
                val u = try {
                    request.url?.toString() ?: return null
                } catch (t: Throwable) {
                    return null
                }
                if (!isVideoRequest(u)) return null
                Log.i(TAG, "intercept media: ${u.take(200)}")
                acceptVideo(u, "", "")
                // 空响应：阻断真实下载，页面继续跑自己的逻辑
                return WebResourceResponse(
                    "video/mp4",
                    "utf-8",
                    ByteArrayInputStream(ByteArray(0))
                )
            }

            override fun onPageFinished(view: WebView, url: String?) {
                main.post { startPolling() }
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError
            ) {
                if (!request.isForMainFrame) return
                val desc = try {
                    error.description?.toString()
                } catch (t: Throwable) {
                    null
                }
                val msg = "页面加载失败：" + (desc ?: "网络错误")
                if (finalRound) bail(msg, "NETWORK") else Log.w(TAG, "round1 load error: $msg")
            }

            override fun onReceivedHttpError(
                view: WebView,
                request: WebResourceRequest,
                errorResponse: WebResourceResponse
            ) {
                if (!request.isForMainFrame) return
                val code = try {
                    errorResponse.statusCode
                } catch (t: Throwable) {
                    -1
                }
                if (code >= 400) {
                    val errCode = when (code) {
                        404, 410 -> "UNAVAILABLE"
                        403, 412, 429 -> "NEEDS_VERIFICATION"
                        else -> "PAGE_LOAD_FAILED"
                    }
                    val msg = when (code) {
                        404, 410 -> "作品不存在或已删除（HTTP $code）"
                        403, 412, 429 -> "页面要求安全验证或访问受限（HTTP $code），请在系统浏览器打开"
                        else -> "页面返回 HTTP $code，可能被风控或视频不存在"
                    }
                    if (finalRound || code == 404 || code == 410) bail(msg, errCode)
                    else Log.w(TAG, "round1 http error: $msg")
                }
            }
        }

        fun loadRound(ua: String, pageToLoad: String, finalRound: Boolean) {
            main.post {
                try {
                    val activity = WebGrabApp.currentActivity
                    val context: Context = activity ?: app
                    val wv = WebView(context)
                    webViewRef.set(wv)
                    configure(wv, buildClient(finalRound), ua)
                    containerRef.set(attach(activity, wv))
                    wv.loadUrl(pageToLoad)
                    // 兜底：即使 onPageFinished 不触发（页面长轮询/被拦），也按时开始读 DOM
                    main.postDelayed({ startPolling() }, 2_000)
                } catch (t: Throwable) {
                    Log.e(TAG, "create webview failed (final=$finalRound)", t)
                    if (finalRound) {
                        bail("无法打开解析页面：" + oneLine(t))
                    }
                }
            }
        }

        fun destroyCurrent() {
            val box = containerRef.getAndSet(null)
            val wv = webViewRef.getAndSet(null)
            if (box == null && wv == null) return
            val cleaned = CountDownLatch(1)
            main.post {
                try {
                    cleanupRefs(box, wv)
                } finally {
                    cleaned.countDown()
                }
            }
            cleaned.await(1_500, TimeUnit.MILLISECONDS)
        }

        try {
            val startedAt = SystemClock.elapsedRealtime()
            // 第一轮：桌面 UA + 原始视频页
            pollGen.incrementAndGet()
            polls = 0
            loadRound(DESKTOP_UA, url, finalRound = false)
            latch.await(ROUND1_TIMEOUT_MS, TimeUnit.MILLISECONDS)

            if (!done.get()) {
                // 第二轮：移动 UA + m 站 share 页，拿 playwm 兜底
                destroyCurrent()
                val shareUrl = mobileShareUrl(url)
                if (shareUrl == null) {
                    bail("没有解析出视频地址（链接里找不到视频 ID）", "SHORT_LINK_UNRESOLVED")
                } else {
                    round2.set(true)
                    val elapsed = SystemClock.elapsedRealtime() - startedAt
                    val remaining = timeoutMs - elapsed
                    if (remaining < 3_000) {
                        bail("解析超时（${timeoutMs / 1000}s）：请检查网络，或视频页需要验证", "DETAIL_TIMEOUT")
                    } else {
                        pollGen.incrementAndGet()
                        polls = 0
                        polling.set(false)
                        graceScheduled.set(false)
                        fallback.set(null)
                        loadRound(MOBILE_UA, shareUrl, finalRound = true)
                        latch.await(remaining, TimeUnit.MILLISECONDS)
                    }
                }
            }

            val v = video.get()
            return if (v.isNullOrBlank()) {
                if (!done.get()) {
                    bail("解析超时（${timeoutMs / 1000}s）：请检查网络，或视频页需要验证", "DETAIL_TIMEOUT")
                }
                failure(error.get() ?: "没有解析出视频地址", errorCode.get() ?: "NO_MEDIA")
            } else {
                success(v, title.get(), cover.get())
            }
        } finally {
            // 一定销毁 WebView：主线程做，最多等 2s
            val cleaned = CountDownLatch(1)
            main.post {
                try {
                    cleanup(webViewRef, containerRef)
                } finally {
                    cleaned.countDown()
                }
            }
            cleaned.await(2, TimeUnit.SECONDS)
        }
    }

    // ------------------------------------------------------------------ WebView

    @SuppressLint("SetJavaScriptEnabled")
    private fun configure(wv: WebView, client: WebViewClient, ua: String) {
        val s = wv.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.mediaPlaybackRequiresUserGesture = false
        s.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        s.userAgentString = ua
        s.cacheMode = WebSettings.LOAD_DEFAULT
        s.javaScriptCanOpenWindowsAutomatically = false
        s.setSupportMultipleWindows(false)
        s.loadsImagesAutomatically = true
        wv.setBackgroundColor(0x00000000)
        wv.isVerticalScrollBarEnabled = false
        wv.isHorizontalScrollBarEnabled = false
        wv.webViewClient = client
        // 部分机型只有设置了 WebChromeClient 才走完整媒体管线
        wv.webChromeClient = WebChromeClient()
    }

    /** 把 1x1 的隐藏 WebView 挂到当前 Activity 上；没有 Activity 时返回 null（游离加载）。 */
    private fun attach(activity: Activity?, wv: WebView): ViewGroup? {
        if (activity == null) {
            Log.w(TAG, "no resumed activity, use detached webview")
            return null
        }
        return try {
            val root = activity.findViewById<ViewGroup>(android.R.id.content) ?: return null
            val box = FrameLayout(activity)
            box.alpha = 0.01f
            box.addView(wv, FrameLayout.LayoutParams(1, 1))
            root.addView(box, ViewGroup.LayoutParams(1, 1))
            box
        } catch (t: Throwable) {
            Log.w(TAG, "attach webview failed, fallback to detached", t)
            null
        }
    }

    private fun cleanup(
        webViewRef: AtomicReference<WebView?>,
        containerRef: AtomicReference<ViewGroup?>
    ) = cleanupRefs(containerRef.getAndSet(null), webViewRef.getAndSet(null))

    private fun cleanupRefs(box: ViewGroup?, wv: WebView?) {
        try {
            if (box != null) {
                (box.parent as? ViewGroup)?.removeView(box)
                box.removeAllViews()
            }
            if (wv != null) {
                try {
                    wv.stopLoading()
                } catch (_: Throwable) {
                }
                try {
                    wv.loadUrl("about:blank")
                } catch (_: Throwable) {
                }
                try {
                    wv.webChromeClient = null
                } catch (_: Throwable) {
                }
                try {
                    wv.webViewClient = WebViewClient()
                } catch (_: Throwable) {
                }
                wv.removeAllViews()
                wv.destroy()
            }
        } catch (t: Throwable) {
            Log.w(TAG, "cleanup failed", t)
        }
    }

    // ------------------------------------------------------------------ 识别

    /** 只认抖音播放 CDN，避免把下载引导页的宣传片当成作品。 */
    internal fun isPlayableCandidate(u: String): Boolean {
        if (!u.startsWith("http", ignoreCase = true)) return false
        val l = u.lowercase()
        if (l.contains("douyin-pc-web") || l.contains("douyinstatic.com")) return false
        return l.contains("douyinvod.com") ||
            l.contains("aweme.snssdk.com/aweme/v1/play") ||
            l.contains("mime_type=video_mp4") ||
            l.contains("/video/tos/") ||
            l.contains("playwm") ||
            l.contains("zjcdn.com")
    }

    /** 请求是否可能是视频本体（douyinvod / mime_type=video_mp4 / m 站 playwm）。 */
    private fun isVideoRequest(u: String): Boolean {
        val l = u.lowercase()
        if (l.contains("mime_type=audio") || l.contains(".mp3") || l.contains(".m4a")) return false
        if (l.contains("playwm")) return true
        if (l.contains("mime_type=video_mp4")) return true
        return l.contains("douyinvod.com")
    }

    /** 无水印直链（douyinvod 的 video_mp4）优先。 */
    private fun isPreferredVideo(u: String): Boolean {
        if (!u.startsWith("http")) return false
        if (isWatermarked(u)) return false
        return u.contains("mime_type=video_mp4") ||
            u.contains("douyinvod.com") ||
            u.contains("/video/tos/")
    }

    private fun isWatermarked(u: String): Boolean =
        u.contains("/playwm/") || u.contains("playwm?") || u.contains("watermark=1")

    private fun normalizeUrl(raw: String): String {
        var u = raw.trim()
        if (u.startsWith("//")) u = "https:$u"
        if (u.startsWith("blob:") || u.startsWith("data:")) return ""
        return u
    }

    /** 从页面地址里取视频 ID，拼 m 站 share 页；取不到返回 null。 */
    private fun mobileShareUrl(pageUrl: String): String? {
        val m = Regex("""(?:/video/|modal_id=|video_id=)(\d{6,})""").find(pageUrl) ?: return null
        val id = m.groupValues[1]
        return "https://m.douyin.com/share/video/$id"
    }

    // ------------------------------------------------------------------ JS

    /** 读当前页面地址 + 可见文字，用于识别登录墙/验证码/访问限制。 */
    private val PAGE_STATE_JS = """
        (function () {
          try {
            var body = document.body ? (document.body.innerText || '') : '';
            return JSON.stringify({
              u: location.href || '',
              t: (document.title || '') + '\n' + body.slice(0, 3000)
            });
          } catch (e) { return ''; }
        })()
    """.trimIndent()

    private val EXTRACT_JS = """
        (function () {
          try {
            var v = document.querySelector('video');
            if (v) { try { var p = v.play(); if (p && p.catch) p.catch(function () {}); } catch (e) {} }
            var src = '';
            if (v) { src = v.currentSrc || v.src || v.getAttribute('src') || ''; }
            if (!src) {
              var s = document.querySelector('video source');
              if (s) { src = s.src || s.getAttribute('src') || ''; }
            }
            var poster = (v && v.poster) || '';
            var og = document.querySelector('meta[property="og:image"]');
            var t = document.title || '';
            if (!t) {
              var ogt = document.querySelector('meta[property="og:title"]');
              t = (ogt && ogt.content) || '';
            }
            return JSON.stringify({
              src: src || '',
              title: t,
              cover: poster || (og && og.content) || ''
            });
          } catch (e) {
            return JSON.stringify({ src: '', title: '', cover: '' });
          }
        })()
    """.trimIndent()

    /** evaluateJavascript 的返回值是「JSON 字符串再包一层 JSON」；解析失败返回 null。 */
    private fun parsePageState(raw: String?): Pair<String, String>? {
        if (raw.isNullOrBlank() || raw == "null" || raw == "undefined" || raw == "\"\"") return null
        return try {
            val first = JSONTokener(raw).nextValue()
            val json = when (first) {
                is String -> JSONObject(first)
                is JSONObject -> first
                else -> return null
            }
            json.optString("u", "") to json.optString("t", "")
        } catch (t: Throwable) {
            Log.w(TAG, "bad page state result: ${raw.take(120)}", t)
            null
        }
    }

    private fun parseExtraction(raw: String?): Triple<String, String, String>? {
        if (raw.isNullOrBlank() || raw == "null" || raw == "undefined") return null
        return try {
            val first = JSONTokener(raw).nextValue()
            val json = when (first) {
                is String -> JSONObject(first)
                is JSONObject -> first
                else -> return null
            }
            Triple(
                json.optString("src", ""),
                json.optString("title", ""),
                json.optString("cover", "")
            )
        } catch (t: Throwable) {
            Log.w(TAG, "bad js result: ${raw.take(160)}", t)
            null
        }
    }

    // ------------------------------------------------------------------ JSON

    private fun success(videoUrl: String, title: String, cover: String): String {
        val watermarked = isWatermarked(videoUrl)
        return JSONObject()
            .put("ok", true)
            .put("video_url", videoUrl)
            .put("title", title)
            .put("cover", cover)
            .put("error", "")
            .put("referer", if (watermarked) REFERER_MOBILE else REFERER_WEB)
            .put("watermark", watermarked)
            .toString()
    }

    private fun failure(msg: String, code: String = ""): String {
        val out = JSONObject().put("ok", false).put("error", msg)
        if (code.isNotBlank()) out.put("error_code", code)
        return out.toString()
    }

    private fun oneLine(t: Throwable): String {
        val msg = (t.message ?: t.javaClass.simpleName).replace('\n', ' ').replace('\r', ' ')
        return if (msg.length > 200) msg.take(200) + "…" else msg
    }
}
