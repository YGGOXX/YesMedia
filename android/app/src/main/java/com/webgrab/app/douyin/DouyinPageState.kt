package com.webgrab.app.douyin

internal enum class DouyinPageKind { OK, LOGIN, CAPTCHA, RESTRICTED, NOT_FOUND }

internal data class DouyinPageState(
    val kind: DouyinPageKind,
    val code: String,
    val message: String
)

/**
 * 识别抖音页面上的登录墙 / 安全验证 / 访问限制。
 *
 * 只做提示，不尝试绕过。宁可漏判也不要误判：公开作品页顶部常有“登录”按钮，
 * 所以裸的“登录”两个字不算登录墙，必须出现“扫码登录 / 请先登录 / 登录后查看”等。
 */
internal object DouyinPageStateDetector {

    private val loginWords = listOf(
        "扫码登录", "请先登录", "登录后查看", "登录后可查看", "登录后继续",
        "log in to continue", "sign in to continue"
    )
    private val captchaWords = listOf(
        "验证码", "安全验证", "完成验证", "拖动滑块", "滑块验证", "人机验证",
        "captcha", "verify you are human"
    )
    private val restrictedWords = listOf(
        "私密作品", "仅自己可见", "无权访问", "没有权限", "访问受限",
        "访问过于频繁", "请求过于频繁", "该内容不可见"
    )
    private val notFoundWords = listOf(
        "作品不存在", "作品已删除", "视频不存在", "视频已删除", "内容不存在",
        "内容已删除", "审核中"
    )

    fun detect(url: String, text: String): DouyinPageState {
        val u = url.lowercase()
        val t = text.lowercase()

        if (u.contains("passport") || u.contains("/login") || loginWords.any { t.contains(it) }) {
            return DouyinPageState(
                DouyinPageKind.LOGIN,
                "NEEDS_LOGIN",
                "这个作品需要登录才能看。请在系统浏览器登录后复制完整地址，或换一个公开作品。"
            )
        }
        if (u.contains("verify") || u.contains("captcha") ||
            captchaWords.any { t.contains(it) }
        ) {
            return DouyinPageState(
                DouyinPageKind.CAPTCHA,
                "NEEDS_VERIFICATION",
                "抖音要求安全验证。请到系统浏览器打开链接完成验证，再复制完整地址回来。"
            )
        }
        if (restrictedWords.any { t.contains(it) }) {
            return DouyinPageState(
                DouyinPageKind.RESTRICTED,
                "UNAVAILABLE",
                "访问被限制或作品不可见。稍后再试，或换一个公开作品。"
            )
        }
        if (notFoundWords.any { t.contains(it) }) {
            return DouyinPageState(
                DouyinPageKind.NOT_FOUND,
                "UNAVAILABLE",
                "作品不存在或已删除。换一个公开作品链接。"
            )
        }
        return DouyinPageState(DouyinPageKind.OK, "", "")
    }
}
