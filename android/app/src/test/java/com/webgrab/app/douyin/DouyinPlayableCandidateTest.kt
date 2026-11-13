package com.webgrab.app.douyin

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DouyinPlayableCandidateTest {

    @Test
    fun `accepts douyin play cdn`() {
        assertTrue(
            DouyinResolver.isPlayableCandidate(
                "https://v11-weba.douyinvod.com/abc/video.mp4?mime_type=video_mp4"
            )
        )
        assertTrue(
            DouyinResolver.isPlayableCandidate(
                "https://aweme.snssdk.com/aweme/v1/play/?video_id=v0200fg10000"
            )
        )
        assertTrue(
            DouyinResolver.isPlayableCandidate(
                "https://m.douyin.com/aweme/v1/playwm/?video_id=v0200fg10000&ratio=720p"
            )
        )
    }

    @Test
    fun `rejects landing page promo video`() {
        assertFalse(
            DouyinResolver.isPlayableCandidate(
                "https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/uuu_265.mp4"
            )
        )
        assertFalse(
            DouyinResolver.isPlayableCandidate(
                "https://p3.douyinpic.com/obj/douyin-pc-web/promo.mp4"
            )
        )
    }

    @Test
    fun `rejects non-http urls`() {
        assertFalse(DouyinResolver.isPlayableCandidate("blob:https://www.douyin.com/xxx"))
        assertFalse(DouyinResolver.isPlayableCandidate(""))
    }
}
