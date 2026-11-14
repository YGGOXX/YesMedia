package com.webgrab.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Process
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * 前台服务：启动 Chaquopy 里的 webgrab.android_entry.start()，本机 127.0.0.1 上跑 HTTP 服务。
 * 状态（starting/running/error/port）写在 SharedPreferences("webgrab")，MainActivity 轮询读取。
 * 通知栏提供「停止」按钮：停服务并结束进程（Python 线程无法从 Java 侧正常 join，杀进程是唯一
 * 可靠释放端口的做法）。
 */
class GrabService : Service() {

    companion object {
        private const val TAG = "YesMedia"
        const val PREFS = "webgrab"
        const val KEY_STATE = "state"
        const val KEY_PORT = "port"
        const val KEY_ERROR = "error"
        const val STATE_STARTING = "starting"
        const val STATE_RUNNING = "running"
        const val STATE_ERROR = "error"
        const val STATE_STOPPED = "stopped"
        const val ACTION_STOP = "com.webgrab.app.action.STOP"
        private const val CHANNEL_ID = "webgrab_service"
        private const val NOTIFICATION_ID = 41
    }

    private val main = Handler(Looper.getMainLooper())
    @Volatile private var workThread: Thread? = null
    @Volatile private var stopping = false

    override fun onCreate() {
        super.onCreate()
        createChannel()
        goForeground(buildNotification("正在启动", "本地服务启动中…", addStop = true))
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopServer()
            return START_NOT_STICKY
        }
        if (stopping) return START_NOT_STICKY
        val running = workThread?.isAlive == true
        if (!running) startWork()
        return START_NOT_STICKY
    }

    @android.annotation.TargetApi(35)
    override fun onTimeout(startId: Int, fgsType: Int) {
        // Android 15 limits dataSync FGS time while the app is backgrounded. Stop promptly;
        // ask the embedded server to release its loopback socket without changing service type.
        Log.w(TAG, "dataSync foreground-service time limit reached; stopping local server")
        stopping = true
        prefs().edit()
            .putString(KEY_STATE, STATE_STOPPED)
            .putInt(KEY_PORT, 0)
            .putString(KEY_ERROR, "后台服务达到系统运行时限，请重新打开 YesMedia 以继续")
            .apply()
        Thread({
            try {
                if (Python.isStarted()) Python.getInstance().getModule("webgrab.server").callAttr("stop")
            } catch (t: Throwable) {
                Log.w(TAG, "server shutdown after FGS timeout failed", t)
            }
        }, "webgrab-timeout-stop").start()
        stopSelf(startId)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        if (!stopping) {
            // 服务被系统回收：保留状态，MainActivity 会重新拉起
            prefs().edit().putString(KEY_STATE, STATE_STOPPED).apply()
        }
    }

    // ---------------------------------------------------------------- 启动工作

    private fun startWork() {
        prefs().edit()
            .putString(KEY_STATE, STATE_STARTING)
            .putInt(KEY_PORT, 0)
            .remove(KEY_ERROR)
            .apply()
        val t = Thread({ doStart() }, "webgrab-python")
        workThread = t
        t.start()
    }

    private fun doStart() {
        try {
            if (!Python.isStarted()) Python.start(AndroidPlatform(applicationContext))
            val staticDir = extractStaticDir()
            // Data root: core/webgrab/config.py reads WEBGRAB_HOME (Path.home() is not
            // writable on Android), so point it at filesDir/webgrab before importing server.
            val home = File(filesDir, "webgrab")
            home.mkdirs()
            val py = Python.getInstance()
            val environ = py.getModule("os").get("environ")
                ?: throw IllegalStateException("Python 环境不可用（os.environ）")
            environ.put("WEBGRAB_HOME", home.absolutePath)
            environ.put("WEBGRAB_STATIC_DIR", staticDir.absolutePath)
            Log.d(
                TAG,
                "calling webgrab.android_entry.start(8765, ${staticDir.absolutePath}) home=$home"
            )
            // core signature: start(port=8765, static_dir=None) -> int
            val port = py.getModule("webgrab.android_entry")
                .callAttr("start", 8765, staticDir.absolutePath)
                .toInt()
            if (port <= 0) throw IllegalStateException("Python 未返回有效端口（$port）")
            prefs().edit()
                .putString(KEY_STATE, STATE_RUNNING)
                .putInt(KEY_PORT, port)
                .remove(KEY_ERROR)
                .apply()
            main.post {
                if (!stopping) {
                    goForeground(
                        buildNotification(
                            "YesMedia运行中",
                            "本地服务已就绪 · 端口 $port",
                            addStop = true
                        )
                    )
                }
            }
        } catch (t: Throwable) {
            Log.e(TAG, "python start failed", t)
            val reason = friendlyMessage(t)
            prefs().edit()
                .putString(KEY_STATE, STATE_ERROR)
                .putString(KEY_ERROR, reason)
                .putInt(KEY_PORT, 0)
                .apply()
            main.post {
                if (!stopping) {
                    goForeground(buildNotification("本地服务启动失败", reason, addStop = true))
                }
            }
        }
    }

    /** 把 assets/webgrab-static 解压到 filesDir/static，返回该目录绝对路径。 */
    private fun extractStaticDir(): File {
        val target = File(filesDir, "static")
        try {
            val assets = assets
            val root = "webgrab-static"
            val entries = assets.list(root) ?: emptyArray()
            if (entries.isEmpty() && !target.exists()) target.mkdirs()
            copyAssetTree(root, target)
        } catch (t: Throwable) {
            Log.w(TAG, "extract static assets failed", t)
        }
        return target
    }

    private fun copyAssetTree(assetPath: String, destDir: File) {
        val children = assets.list(assetPath) ?: return
        if (children.isEmpty()) return
        destDir.mkdirs()
        for (child in children) {
            val childAsset = "$assetPath/$child"
            val grandChildren = assets.list(childAsset) ?: emptyArray()
            if (grandChildren.isEmpty()) {
                val file = File(destDir, child)
                file.parentFile?.mkdirs()
                assets.open(childAsset).use { input ->
                    file.outputStream().use { output -> input.copyTo(output) }
                }
            } else {
                copyAssetTree(childAsset, File(destDir, child))
            }
        }
    }

    // ---------------------------------------------------------------- 停止

    private fun stopServer() {
        stopping = true
        prefs().edit()
            .putString(KEY_STATE, STATE_STOPPED)
            .putInt(KEY_PORT, 0)
            .apply()
        try {
            stopForeground(Service.STOP_FOREGROUND_REMOVE)
        } catch (_: Throwable) {
        }
        stopSelf()
        // Python 服务线程仍在跑，直接结束进程才能释放端口
        main.postDelayed({
            Process.killProcess(Process.myPid())
        }, 300)
    }

    // ---------------------------------------------------------------- 通知

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = getString(R.string.notification_channel_desc)
            setShowBadge(false)
            enableVibration(false)
        }
        nm.createNotificationChannel(channel)
    }

    private fun goForeground(notification: Notification) {
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun buildNotification(title: String, text: String, addStop: Boolean): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        builder
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setShowWhen(false)
            .setContentIntent(openIntent)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setVisibility(Notification.VISIBILITY_PUBLIC)
        if (addStop) {
            val stopIntent = PendingIntent.getService(
                this,
                1,
                Intent(this, GrabService::class.java).setAction(ACTION_STOP),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            builder.addAction(
                @Suppress("DEPRECATION")
                Notification.Action.Builder(
                    android.R.drawable.ic_menu_close_clear_cancel,
                    getString(R.string.notification_stop),
                    stopIntent
                ).build()
            )
        }
        return builder.build()
    }

    private fun prefs() = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
