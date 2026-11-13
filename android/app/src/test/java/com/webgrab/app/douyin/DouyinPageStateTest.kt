package com.webgrab.app.douyin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class DouyinPageStateTest {

    @Test
    fun publicPageIsOk() {
        val state = DouyinPageStateDetector.detect(
            "https://www.douyin.com/video/7412345678901234567",
            "作品标题\n点赞 1.2万 评论 345"
        )
        assertEquals(DouyinPageKind.OK, state.kind)
    }

    @Test
    fun bareLoginWordIsNotALoginWall() {
        val state = DouyinPageStateDetector.detect(
            "https://www.douyin.com/video/7412345678901234567",
            "登录 首页 推荐 关注"
        )
        assertEquals(DouyinPageKind.OK, state.kind)
    }

    @Test
    fun loginWallIsDetected() {
        val state = DouyinPageStateDetector.detect(
            "https://www.douyin.com/video/1",
            "请先登录后查看"
        )
        assertEquals(DouyinPageKind.LOGIN, state.kind)
        assertEquals(DouyinErrorCode.NEEDS_LOGIN, state.code)
    }

    @Test
    fun captchaIsDetected() {
        val state = DouyinPageStateDetector.detect(
            "https://www.douyin.com/verify",
            "请完成安全验证"
        )
        assertEquals(DouyinPageKind.CAPTCHA, state.kind)
        assertEquals(DouyinErrorCode.NEEDS_VERIFICATION, state.code)
    }

    @Test
    fun restrictedIsDetected() {
        val state = DouyinPageStateDetector.detect(
            "https://www.douyin.com/video/1",
            "该内容不可见"
        )
        assertEquals(DouyinPageKind.RESTRICTED, state.kind)
        assertEquals(DouyinErrorCode.UNAVAILABLE, state.code)
    }

    @Test
    fun deletedIsDetected() {
        val state = DouyinPageStateDetector.detect(
            "https://www.douyin.com/video/1",
            "作品已删除"
        )
        val expected = DouyinPageKind.NOT_FOUND
        assertEquals(expected, state.kind)
        assertTrue(state.message.contains("换"))
    }
}
