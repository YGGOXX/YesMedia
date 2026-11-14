package com.webgrab.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 单 Activity 外壳：内嵌 WebView 显示本机 127.0.0.1:<port> 上的 WebGrab 前端。
 * Python 服务跑在 [GrabService] 里；本 Activity 只负责等 /api/health 就绪、加载页面、
 * 接系统分享文本、把网页下载交给 DownloadManager，并把文件保存到 下载/YesMedia/。
 */
@Suppress("DEPRECATION")
class MainActivity : Activity() {

    companion object {
        private const val TAG = "WebGrab"
        const val USER_AGENT_SUFFIX = "YesMediaAndroid/1.0.1"
        private const val REQ_FILE_CHOOSER = 1001
        private const val REQ_POST_NOTIFICATIONS = 1002
        private const val REQ_WRITE_STORAGE = 1003
        private const val DOWNLOAD_SUBDIR = "YesMedia"
    }

    private lateinit var root: FrameLayout
    private lateinit var webView: WebView
    @Volatile private var serverPort = 0
    private var pendingShareText: String? = null
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var pendingDownload: PendingDownload? = null
    private val io = Executors.newSingleThreadExecutor { r ->
        Thread(r, "webgrab-wait").apply { isDaemon = true }
    }
    private val main = Handler(Looper.getMainLooper())
    private val serviceStarted = AtomicBoolean(false)

    private data class PendingDownload(
        val url: String,
        val userAgent: String?,
        val contentDisposition: String?,
        val mimeType: String?,
    )

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE)

        root = FrameLayout(this)
        root.setBackgroundColor(0xFF10131A.toInt())
        webView = WebView(this)
        root.addView(
            webView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        setContentView(root)

        applyEdgeToEdge()
        configureWebView()

        pendingShareText = extractSharedText(intent)
        startServiceIfNeeded()
        waitForServer()
    }

    // ---------------------------------------------------------------- 边缘到边缘

    private fun applyEdgeToEdge() {
        if (Build.VERSION.SDK_INT >= 30) {
            window.setDecorFitsSystemWindows(false)
            root.setOnApplyWindowInsetsListener { _, insets ->
                val i = insets.getInsets(
                    WindowInsets.Type.systemBars() or WindowInsets.Type.ime()
                )
                root.setPadding(i.left, i.top, i.right, i.bottom)
                insets
            }
        } else {
            window.decorView.systemUiVisibility =
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                    View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
                    View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
            root.setOnApplyWindowInsetsListener { _, insets ->
                root.setPadding(
                    insets.systemWindowInsetLeft,
                    insets.systemWindowInsetTop,
                    insets.systemWindowInsetRight,
                    insets.systemWindowInsetBottom
                )
                insets
            }
        }
    }

    // ---------------------------------------------------------------- WebView 配置

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        val s = webView.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.databaseEnabled = true
        s.cacheMode = WebSettings.LOAD_DEFAULT
        s.mediaPlaybackRequiresUserGesture = false
        s.setSupportZoom(false)
        s.builtInZoomControls = false
        s.useWideViewPort = true
        s.loadWithOverviewMode = true
        s.allowFileAccess = false
        s.allowContentAccess = true
        s.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        s.userAgentString = WebSettings.getDefaultUserAgent(this) + " " + USER_AGENT_SUFFIX

        CookieManager.getInstance().setAcceptCookie(true)

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest
            ): Boolean = openExternalIfNeeded(request.url)

            override fun shouldOverrideUrlLoading(view: WebView, url: String?): Boolean =
                url != null && openExternalIfNeeded(Uri.parse(url))

            override fun onPageFinished(view: WebView, url: String?) {
                Log.d(TAG, "onPageFinished: $url")
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                // 页面内需要摄像头/麦克风（录音）时直接放行本机页面
                val ok = false // Web page capture permissions are disabled in this release.
                    it == PermissionRequest.RESOURCE_AUDIO_CAPTURE ||
                        it == PermissionRequest.RESOURCE_VIDEO_CAPTURE
                }
                request.deny()
            }

            override fun onShowFileChooser(
                webView: WebView,
                callback: ValueCallback<Array<Uri>>,
                params: FileChooserParams
            ): Boolean {
                filePathCallback?.onReceiveValue(null)
                filePathCallback = callback
                return try {
                    startActivityForResult(params.createIntent(), REQ_FILE_CHOOSER)
                    true
                } catch (e: ActivityNotFoundException) {
                    filePathCallback = null
                    toast("没有可用的文件选择器")
                    false
                }
            }
        }

        webView.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            enqueueDownload(url, userAgent, contentDisposition, mimeType)
        }
    }

    /** 返回 true 表示本次导航已被处理（跳外部 App），false 表示交给 WebView 加载。 */
    private fun openExternalIfNeeded(uri: Uri): Boolean {
        val scheme = uri.scheme?.lowercase() ?: return false
        if (scheme == "http" && uri.host == "127.0.0.1" && uri.port == serverPort && serverPort > 0) return false
        return try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
            true
        } catch (e: ActivityNotFoundException) {
            toast("无法打开该链接")
            true
        }
    }

    // ---------------------------------------------------------------- 服务启动与等待

    private fun startServiceIfNeeded() {
        if (Build.VERSION.SDK_INT >= 33) {
            if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
                PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    REQ_POST_NOTIFICATIONS
                )
            }
        }
        if (!serviceStarted.compareAndSet(false, true)) return
        val intent = Intent(this, GrabService::class.java)
        try {
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
        } catch (t: Throwable) {
            serviceStarted.set(false)
            Log.e(TAG, "startForegroundService failed", t)
            toast("无法启动本地服务：${friendlyMessage(t)}")
        }
    }

    private fun waitForServer() {
        io.execute {
            val prefs = getSharedPreferences(GrabService.PREFS, Context.MODE_PRIVATE)
            val deadline = System.currentTimeMillis() + 60_000
            var port = 0
            var lastError: String? = null
            while (System.currentTimeMillis() < deadline) {
                val state = prefs.getString(GrabService.KEY_STATE, null)
                val p = prefs.getInt(GrabService.KEY_PORT, 0)
                lastError = prefs.getString(GrabService.KEY_ERROR, null) ?: lastError
                if (state == GrabService.STATE_ERROR) break
                if (p > 0 && healthOk(p)) {
                    port = p
                    break
                }
                if (!serviceStarted.get()) break
                try {
                    Thread.sleep(400)
                } catch (e: InterruptedException) {
                    return@execute
                }
            }
            if (port > 0) {
                serverPort = port
                main.post { loadStartPage() }
            } else {
                main.post { showStartError(lastError ?: "本地服务启动超时（60 秒）") }
            }
        }
    }

    private fun healthOk(port: Int): Boolean {
        var conn: HttpURLConnection? = null
        return try {
            conn = URL("http://127.0.0.1:$port/api/health").openConnection() as HttpURLConnection
            conn.connectTimeout = 400
            conn.readTimeout = 800
            conn.useCaches = false
            val body = if (conn.responseCode == 200) {
                conn.inputStream.bufferedReader().use { it.readText() }
            } else ""
            body.contains("\"ok\"")
        } catch (t: Throwable) {
            false
        } finally {
            try {
                conn?.disconnect()
            } catch (_: Throwable) {
            }
        }
    }

    private fun loadStartPage() {
        val share = pendingShareText
        if (share != null) {
            pendingShareText = null
            webView.loadUrl(shareUrl(share))
        } else {
            webView.loadUrl(baseUrl())
        }
    }

    private fun baseUrl(): String = "http://127.0.0.1:$serverPort/"

    private fun shareUrl(text: String): String {
        val found = Regex("https?://[^\\s\"'<>]+").find(text)?.value.orEmpty()
        val encodedText = URLEncoder.encode(text, "UTF-8")
        val sb = StringBuilder(baseUrl()).append("share?text=").append(encodedText)
        if (found.isNotBlank()) sb.append("&url=").append(URLEncoder.encode(found, "UTF-8"))
        return sb.toString()
    }

    private fun showStartError(reason: String) {
        if (isFinishing) return
        root.removeAllViews()
        webView.destroy()
        val tv = TextView(this)
        tv.setTextColor(0xFFE6E8EF.toInt())
        tv.gravity = Gravity.CENTER
        tv.textSize = 16f
        tv.text = "无法启动本地服务\n\n$reason\n\n（点一下重试）"
        tv.setOnClickListener {
            root.removeView(tv)
            webView = WebView(this)
            root.addView(
                webView,
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT
                )
            )
            configureWebView()
            serverPort = 0
            serviceStarted.set(false)
            startServiceIfNeeded()
            waitForServer()
        }
        root.addView(
            tv,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
    }

    // ---------------------------------------------------------------- 分享入口

    private fun extractSharedText(intent: Intent?): String? {
        if (intent == null || intent.action != Intent.ACTION_SEND) return null
        if (intent.type?.startsWith("text/") != true) return null
        val text = intent.getStringExtra(Intent.EXTRA_TEXT)?.trim()
        return text?.takeIf { it.isNotEmpty() }
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        setIntent(intent)
        val text = extractSharedText(intent) ?: return
        if (serverPort > 0) {
            webView.loadUrl(shareUrl(text))
        } else {
            pendingShareText = text
        }
    }

    // ---------------------------------------------------------------- 下载

    private fun enqueueDownload(
        url: String,
        userAgent: String?,
        contentDisposition: String?,
        mimeType: String?,
    ) {
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            toast("暂不支持下载该链接")
            return
        }
        if (Build.VERSION.SDK_INT < 29 &&
            checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            pendingDownload = PendingDownload(url, userAgent, contentDisposition, mimeType)
            requestPermissions(
                arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE),
                REQ_WRITE_STORAGE
            )
            return
        }
        try {
            val name = guessFileName(url, contentDisposition, mimeType)
            val request = DownloadManager.Request(Uri.parse(url))
            if (!mimeType.isNullOrBlank()) request.setMimeType(mimeType)
            request.setTitle(name)
            request.setDescription(DOWNLOAD_SUBDIR)
            request.addRequestHeader(
                "User-Agent",
                userAgent?.takeIf { it.isNotBlank() } ?: webView.settings.userAgentString
            )
            CookieManager.getInstance().getCookie(url)?.let {
                if (it.isNotBlank()) request.addRequestHeader("Cookie", it)
            }
            request.setNotificationVisibility(
                DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED
            )
            request.setAllowedOverMetered(true)
            request.setAllowedOverRoaming(true)
            // Android 10+ 的下载暂存（IS_PENDING）由 DownloadManager 内部处理
            request.setDestinationInExternalPublicDir(
                Environment.DIRECTORY_DOWNLOADS,
                "$DOWNLOAD_SUBDIR/$name"
            )
            val dm = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            dm.enqueue(request)
            toast("已开始下载：$name")
        } catch (t: Throwable) {
            Log.e(TAG, "enqueue download failed", t)
            toast("下载失败：${friendlyMessage(t)}")
        }
    }

    private fun guessFileName(url: String, cd: String?, mime: String?): String {
        var name = ""
        if (!cd.isNullOrBlank()) {
            name = Regex("filename\\*=[^']*''([^;]+)", RegexOption.IGNORE_CASE)
                .find(cd)?.groupValues?.get(1)
                ?: Regex("filename=\"?([^\";]+)\"?", RegexOption.IGNORE_CASE)
                    .find(cd)?.groupValues?.get(1)
                    ?: ""
            name = runCatching { Uri.decode(name) }.getOrDefault(name)
        }
        if (name.isBlank()) {
            name = runCatching { Uri.parse(url).getQueryParameter("name") }
                .getOrNull().orEmpty()
        }
        if (name.isBlank()) name = URLUtil.guessFileName(url, cd, mime)
        name = sanitizeFileName(name)
        if (name.isBlank()) name = "webgrab_${System.currentTimeMillis()}"
        return name
    }

    private fun sanitizeFileName(raw: String): String {
        var n = raw.replace(Regex("[\\\\/:*?\"<>|\\p{Cntrl}]"), "_").trim().trim('.')
        if (n.length > 120) {
            val dot = n.lastIndexOf('.')
            n = if (dot > 0 && n.length - dot <= 8) n.substring(0, 100) + n.substring(dot)
            else n.substring(0, 120)
        }
        return n
    }

    // ---------------------------------------------------------------- 文件选择 / 权限

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_FILE_CHOOSER) {
            val cb = filePathCallback
            filePathCallback = null
            if (cb == null) return
            val result = if (resultCode == RESULT_OK && data != null) {
                WebChromeClient.FileChooserParams.parseResult(resultCode, data)
            } else null
            cb.onReceiveValue(result)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        when (requestCode) {
            REQ_POST_NOTIFICATIONS -> {
                if (grantResults.firstOrNull() != PackageManager.PERMISSION_GRANTED) {
                    toast("未开启通知权限，服务状态只能在本应用内查看")
                }
            }
            REQ_WRITE_STORAGE -> {
                val pending = pendingDownload
                pendingDownload = null
                if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED && pending != null) {
                    enqueueDownload(
                        pending.url, pending.userAgent,
                        pending.contentDisposition, pending.mimeType
                    )
                } else {
                    toast("未授予存储权限，无法保存到「下载/YesMedia」")
                }
            }
        }
    }

    // ---------------------------------------------------------------- 生命周期

    override fun onBackPressed() {
        if (::webView.isInitialized && webView.canGoBack()) webView.goBack()
        else moveTaskToBack(true)
    }

    override fun onDestroy() {
        io.shutdownNow()
        if (::webView.isInitialized) {
            try {
                webView.stopLoading()
                webView.destroy()
            } catch (_: Throwable) {
            }
        }
        super.onDestroy()
    }

    // ---------------------------------------------------------------- 工具

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
