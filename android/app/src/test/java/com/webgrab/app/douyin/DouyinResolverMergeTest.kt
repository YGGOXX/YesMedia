package com.webgrab.app.douyin

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DouyinResolverMergeTest {

    @Test
    fun nativeFailureKeepsLegacyMessageAndAddsCodes() {
        val native = """{"ok":false,"error_code":"NETWORK","error":"网络连接中断"}"""
        val legacy = """{"ok":false,"error":"抖音解析失败：页面加载超时"}"""

        val merged = JSONObject(DouyinResolver.mergeFailure(native, legacy))

        assertFalse(merged.getBoolean("ok"))
        assertEquals("抖音解析失败：页面加载超时", merged.getString("error"))
        assertEquals("NETWORK", merged.getString("error_code"))
        assertEquals("NETWORK", merged.getString("native_error_code"))
        assertEquals("网络连接中断", merged.getString("native_error"))
    }

    @Test
    fun legacySuccessIsReturnedUnchanged() {
        val native = """{"ok":false,"error_code":"UNAVAILABLE"}"""
        val legacy = """{"ok":true,"video_url":"https://v.example/a.mp4"}"""

        assertEquals(legacy, DouyinResolver.mergeFailure(native, legacy))
    }

    @Test
    fun brokenLegacyJsonFallsBackToItself() {
        assertEquals("not-json", DouyinResolver.mergeFailure("{}", "not-json"))
    }

    @Test
    fun missingNativeCodeFallsBackToNoMedia() {
        val legacy = """{"ok":false,"error":"没有找到可用媒体"}"""
        val merged = JSONObject(DouyinResolver.mergeFailure("""{"ok":false}""", legacy))

        assertEquals("NO_MEDIA", merged.getString("error_code"))
        assertEquals("没有找到可用媒体", merged.getString("error"))
        assertTrue(merged.has("error"))
    }

    @Test
    fun structureCodeIsPreserved() {
        val native = """{"ok":false,"error_code":"PAGE_STRUCTURE_CHANGED","error":"页面结构变了"}"""
        val legacy = """{"ok":false,"error":"抖音解析失败：直连无数据"}"""
        val merged = JSONObject(DouyinResolver.mergeFailure(native, legacy))

        assertEquals("PAGE_STRUCTURE_CHANGED", merged.getString("error_code"))
        assertEquals("页面结构变了", merged.getString("native_error"))
    }
}
