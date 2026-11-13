package com.webgrab.app

/** 把异常翻译成中文可读原因（契约 §10：所有面向用户的错误必须是中文）。 */
internal fun friendlyMessage(t: Throwable): String {
    val raw = generateSequence(t) { it.cause }
        .mapNotNull { it.message?.trim()?.takeIf { m -> m.isNotEmpty() } }
        .firstOrNull()
        ?: t.javaClass.simpleName
    val hint = when (t) {
        is UnsatisfiedLinkError -> "原生库加载失败"
        is NoClassDefFoundError -> "缺少运行依赖"
        is java.net.BindException -> "端口被占用"
        is java.io.IOException -> "读写失败"
        else -> null
    }
    val text = if (hint != null) "$hint：$raw" else raw
    return if (text.length > 240) text.take(240) + "…" else text
}
