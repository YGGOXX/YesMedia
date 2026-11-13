package com.webgrab.app.douyin

import org.json.JSONObject
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ExecutionException
import java.util.concurrent.FutureTask
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

/**
 * 同一链接只允许一个隐藏 WebView 解析任务；并发调用共享第一个任务的结果。
 *
 * Python 侧在 SSE 与 POST 回退、重复点击等情况下可能同时调进来两次；
 * 没有这道闸门就会创建两个隐藏 WebView，既浪费内存也容易触发风控。
 */
internal class LegacyParseGate {

    private val inFlight = ConcurrentHashMap<String, FutureTask<String>>()

    fun run(key: String, waitMs: Long, block: () -> String): String {
        val task = FutureTask(block)
        val prev = inFlight.putIfAbsent(key, task)
        if (prev != null) {
            return await(prev, waitMs)
        }
        return try {
            task.run()
            task.get()
        } catch (e: ExecutionException) {
            errorJson("PAGE_LOAD_FAILED", "解析失败：" + oneLine(e.cause ?: e))
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
            errorJson("PAGE_LOAD_FAILED", "解析被中断，请重试")
        } finally {
            inFlight.remove(key, task)
        }
    }

    private fun await(task: FutureTask<String>, waitMs: Long): String = try {
        task.get(waitMs.coerceIn(1_000L, 60_000L), TimeUnit.MILLISECONDS)
    } catch (e: TimeoutException) {
        errorJson("DETAIL_TIMEOUT", "同一链接还在解析，请稍后重试")
    } catch (e: ExecutionException) {
        errorJson("PAGE_LOAD_FAILED", "解析失败：" + oneLine(e.cause ?: e))
    } catch (e: InterruptedException) {
        Thread.currentThread().interrupt()
        errorJson("PAGE_LOAD_FAILED", "解析被中断，请重试")
    }

    private fun errorJson(code: String, message: String): String =
        JSONObject().put("ok", false).put("error_code", code).put("error", message).toString()

    private fun oneLine(t: Throwable): String {
        val msg = (t.message ?: t.javaClass.simpleName).replace('\n', ' ').replace('\r', ' ')
        return if (msg.length > 160) msg.take(160) + "…" else msg
    }
}
