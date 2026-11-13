package com.webgrab.app

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.webkit.WebView

class WebGrabApp : Application() {

    override fun onCreate() {
        super.onCreate()
        instanceOrNull = this

        // 调试构建允许 chrome://inspect 连线看 WebView
        if (BuildConfig.DEBUG) {
            WebView.setWebContentsDebuggingEnabled(true)
        }

        // 抖音解析需要在真实 Activity 里挂一个 1x1 WebView（部分机型对游离 WebView
        // 不渲染/不加载视频），这里记录当前可见 Activity，解析完立即移除。
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
            override fun onActivityStarted(activity: Activity) {}
            override fun onActivityResumed(activity: Activity) {
                currentActivity = activity
            }

            override fun onActivityPaused(activity: Activity) {
                if (currentActivity === activity) currentActivity = null
            }

            override fun onActivityStopped(activity: Activity) {}
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {
                if (currentActivity === activity) currentActivity = null
            }
        })
    }

    companion object {
        @Volatile
        private var instanceOrNull: WebGrabApp? = null

        val instance: WebGrabApp
            get() = instanceOrNull ?: throw IllegalStateException("WebGrabApp 未初始化")

        /** 当前处于 resumed 状态的 Activity，可能为 null（后台/启动中）。 */
        @Volatile
        var currentActivity: Activity? = null
            private set
    }
}
