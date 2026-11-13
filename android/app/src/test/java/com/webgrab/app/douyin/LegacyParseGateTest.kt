package com.webgrab.app.douyin

import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.assertEquals
import org.junit.Test

class LegacyParseGateTest {

    @Test
    fun concurrentCallsShareOneWebViewTask() {
        val gate = LegacyParseGate()
        val calls = AtomicInteger(0)
        val start = CountDownLatch(1)
        val results = Collections.synchronizedList(mutableListOf<String>())
        val json = """{"ok":true,"video_url":"https://v.example/a.mp4"}"""

        val block = {
            calls.incrementAndGet()
            Thread.sleep(250)
            json
        }

        val first = Thread {
            start.await()
            results.add(gate.run("https://www.douyin.com/video/1", 5_000) { block() })
        }
        val second = Thread {
            start.await()
            results.add(gate.run("https://www.douyin.com/video/1", 5_000) { block() })
        }
        first.start()
        second.start()
        start.countDown()
        first.join(5_000)
        second.join(5_000)

        assertEquals(1, calls.get())
        assertEquals(2, results.size)
        assertEquals(json, results[0])
        assertEquals(json, results[1])
    }

    @Test
    fun differentLinksRunSeparately() {
        val gate = LegacyParseGate()
        val calls = AtomicInteger(0)
        val a = gate.run("a", 1_000) { calls.incrementAndGet(); """{"ok":true}""" }
        val b = gate.run("b", 1_000) { calls.incrementAndGet(); """{"ok":true}""" }

        assertEquals(2, calls.get())
        assertEquals(a, b)
    }

    @Test
    fun failureIsReturnedAsJson() {
        val gate = LegacyParseGate()
        val out = gate.run("x", 1_000) { throw IllegalStateException("boom") }

        assert(out.contains("\"ok\":false"))
        assert(out.contains("boom"))
    }
}
