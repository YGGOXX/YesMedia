package com.webgrab.app.douyin

/**
 * 抖音解析错误码。与 docs/推荐技术架构.md §3 对齐。
 * Kotlin 侧只产出这些 code；Python 侧原样透传给前端。
 */
object DouyinErrorCode {
    const val NO_LINK = "NO_LINK"
    const val INVALID_INPUT = "INVALID_INPUT"
    const val UNSUPPORTED_HOST = "UNSUPPORTED_HOST"
    const val SHORT_LINK_FAILED = "SHORT_LINK_FAILED"
    const val SHORT_LINK_TIMEOUT = "SHORT_LINK_TIMEOUT"
    const val SHORT_LINK_UNRESOLVED = "SHORT_LINK_UNRESOLVED"
    const val PAGE_LOAD_FAILED = "PAGE_LOAD_FAILED"
    const val PAGE_STRUCTURE_CHANGED = "PAGE_STRUCTURE_CHANGED"
    const val UNAVAILABLE = "UNAVAILABLE"
    const val MEDIA_UNSUPPORTED = "MEDIA_UNSUPPORTED"
    const val DETAIL_TIMEOUT = "DETAIL_TIMEOUT"
    const val NO_MEDIA = "NO_MEDIA"
    const val NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
    const val NEEDS_LOGIN = "NEEDS_LOGIN"
    const val NETWORK = "NETWORK"
}

/** 带错误码的解析异常；message 是可直接展示的中文。 */
class DouyinException(
    val code: String,
    override val message: String,
    cause: Throwable? = null,
) : Exception(message, cause)
