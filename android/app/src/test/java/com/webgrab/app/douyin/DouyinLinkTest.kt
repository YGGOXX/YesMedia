package com.webgrab.app.douyin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DouyinLinkTest {

    @Test
    fun `extracts direct video link from share text`() {
        val text = "看看这个 https://www.douyin.com/video/7388149561765760266 很有趣"
        val target = DouyinLink.extractLocal(text)
        assertNotNull(target)
        val t = target!!
        assertEquals("7388149561765760266", t.awemeId)
        assertEquals("video", t.kind)
        assertEquals("https://www.douyin.com/video/7388149561765760266", t.pageUrl)
    }

    @Test
    fun `extracts note link`() {
        val target = DouyinLink.extractLocal("https://www.douyin.com/note/7412345678901234567")
        assertNotNull(target)
        val t = target!!
        assertEquals("note", t.kind)
        assertEquals("7412345678901234567", t.awemeId)
    }

    @Test
    fun `recognizes short link without network`() {
        val target = DouyinLink.extractLocal("复制打开抖音 https://v.douyin.com/AbCdEf-1/ 看看")
        assertNotNull(target)
        assertEquals("short", target!!.kind)
    }

    @Test
    fun `accepts pure aweme id`() {
        val target = DouyinLink.extractLocal("7388149561765760266")
        assertNotNull(target)
        assertEquals("https://www.douyin.com/video/7388149561765760266", target!!.pageUrl)
    }

    @Test
    fun `rejects non douyin text`() {
        assertNull(DouyinLink.extractLocal("https://example.com/video/123"))
    }

    @Test
    fun `rejects unsupported host`() {
        val resolved = DouyinNativeResolver.resolve("https://example.com/video/123")
        assertTrue(resolved.contains("\"ok\":false"))
        assertTrue(resolved.contains(DouyinErrorCode.NO_LINK))
    }

    @Test
    fun `direct parser keeps query string out of page url`() {
        val target = DouyinLink.parseDirect(
            "https://www.douyin.com/video/7388149561765760266?previous_page=app_code_link"
        )
        assertNotNull(target)
        assertEquals(
            "https://www.douyin.com/video/7388149561765760266",
            target!!.pageUrl,
        )
    }
}
