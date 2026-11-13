package com.webgrab.app.douyin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RouterDataParserTest {

    private val videoHtml = """
        <html><head><script>
        window._ROUTER_DATA = {
          "loaderData": {
            "video_(id)/page": {
              "videoInfoRes": {
                "item_list": [{
                  "aweme_id": "7388149561765760266",
                  "desc": "海边的黄昏",
                  "author": {"nickname": "测试作者"},
                  "video": {
                    "play_addr": {
                      "url_list": ["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0200fg10000"],
                      "width": 720,
                      "height": 1280,
                      "data_size": 420828761
                    },
                    "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpg"]},
                    "duration": 62000
                  }
                }]
              }
            }
          }
        };
        </script></head><body></body></html>
    """.trimIndent()

    private val galleryHtml = """
        <script>
        window._ROUTER_DATA = {"loaderData":{"note_(id)/page":{"videoInfoRes":{"item_list":[{
          "aweme_id":"7412345678901234567",
          "desc":"图集标题",
          "author":{"nickname":"图集作者"},
          "images":[
            {"url_list":["https://p3.douyinpic.com/a.jpg"],"width":1320,"height":1750},
            {"url_list":["https://p3.douyinpic.com/b.jpg"],"width":1320,"height":1750,
             "video":{"play_addr":{"url_list":["https://v3.douyinvod.com/live.mp4"]}}}
          ]
        }]}}}}</script>
    """.trimIndent()

    @Test
    fun `parses video share page`() {
        val media = RouterDataParser.parse(videoHtml)
        assertNotNull(media)
        val m = media!!
        assertEquals("7388149561765760266", m.awemeId)
        assertEquals("海边的黄昏", m.title)
        assertEquals("测试作者", m.author)
        assertEquals(720, m.width)
        assertEquals(1280, m.height)
        assertEquals(420828761L, m.size)
        assertEquals(62000L, m.durationMs)
        assertEquals("https://p3.douyinpic.com/cover.jpg", m.cover)
        assertTrue(m.playUrl.contains("/play/"))
        assertTrue(m.playWmUrl.contains("/playwm/"))
        assertTrue(m.images.isEmpty())
    }

    @Test
    fun `parses gallery with live photo`() {
        val media = RouterDataParser.parse(galleryHtml)
        assertNotNull(media)
        val m = media!!
        assertEquals(2, m.images.size)
        assertEquals("https://p3.douyinpic.com/a.jpg", m.images[0].url)
        assertEquals(1320, m.images[0].width)
        assertEquals("https://v3.douyinvod.com/live.mp4", m.images[1].livePhotoUrl)
        assertTrue(m.playUrl.isBlank())
    }

    @Test
    fun `returns null for malformed html`() {
        assertNull(RouterDataParser.parse("<html>no router data</html>"))
    }

    @Test
    fun `gallery ignores slideshow play url with embedded http`() {
        val html = """
            <script>
            window._ROUTER_DATA = {"loaderData":{"note_(id)/page":{"item_list":[{
              "aweme_id":"7386972995153120548",
              "desc":"图集作品",
              "images":[
                {"url_list":["https://p5-ex-gddgtc-sign.douyinpic.com/a.jpg"]},
                {"url_list":["https://p5-ex-gddgtc-sign.douyinpic.com/b.jpg"]}
              ],
              "video":{"play_addr":{"url_list":["https://aweme.snssdk.com/aweme/v1/play/?video_id=https://sf26-sign.douyinvod.com/x.mp4"]}}
            }]}}}
            </script>
        """.trimIndent()
        val m = RouterDataParser.parse(html)
        assertNotNull(m)
        assertEquals(2, m!!.images.size)
        assertTrue(m.playUrl.isBlank())
    }

    @Test
    fun `expected aweme id ignores promo video`() {
        val html = """
            <script>
            window._ROUTER_DATA = {"loaderData":{"landing":{"item_list":[
              {"aweme_id":"1111111111111111111","desc":"下载引导",
               "video":{"play_addr":{"url_list":["https://lf-douyin-pc-web.douyinstatic.com/obj/douyin-pc-web/uuu_265.mp4"]}}},
              {"aweme_id":"7388149561765760266","desc":"目标作品",
               "video":{"play_addr":{"url_list":["https://aweme.snssdk.com/aweme/v1/play/?video_id=ok"]}}}
            ]}}}
            </script>
        """.trimIndent()
        // 请求的作品不在页面里：不能把引导页宣传视频当成结果。
        assertNull(RouterDataParser.parse(html, "7999999999999999999"))
        val m = RouterDataParser.parse(html, "7388149561765760266")
        assertNotNull(m)
        assertEquals("目标作品", m!!.title)
        assertTrue(m.playUrl.contains("aweme.snssdk.com"))
    }

    @Test
    fun `extract handles nested braces inside strings`() {
        val html = """window._ROUTER_DATA = {"a":{"desc":"含 } 的标题"},"b":1,"c":"padding-value"};"""
        val json = RouterDataParser.extractRouterJson(html)
        assertNotNull(json)
        assertTrue(json!!.endsWith("}"))
        assertTrue(json.contains("含 } 的标题"))
    }
}
