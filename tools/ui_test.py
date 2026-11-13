"""移动端 UI 端到端检查（Playwright + 本机 Edge，不下载 Chromium）。

覆盖：
  1. 首次打开自动跑示例，解析出 5 项
  2. 预览：打开 / 切换 / 双击缩放 / Esc 关闭
  3. 选中计数、分类全选
  4. 批量下载（hook HTMLAnchorElement.prototype.click 计数，不真下载）
  5. 上传 demo.mp4 转 mp3，出下载链接
  6. /share?url= 带参自动解析并 replaceState('/')
  7. 360/393/430 三档屏宽无横向溢出
  8. 布局自解释：无教学卡、转音频常驻、帮助弹层（换源开关 / 接口设置）
  9. 抖音直链带 page=&via=dy，普通素材参数不变

用法：
    .venv\\Scripts\\python.exe tools\\ui_test.py
    .venv\\Scripts\\python.exe tools\\ui_test.py --base http://127.0.0.1:8765
    .venv\\Scripts\\python.exe tools\\ui_test.py --headed --shots .tmp\\ui
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]

# 控制台编码（GBK 的 Windows 终端）遇到 ‹ › 之类字符会抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

# Windows 上 Playwright 要 mkdtemp；某些环境默认 TEMP 不可写，统一指到仓库内。
_TMP = ROOT / ".tmp" / "pw"
_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = str(_TMP)

try:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    print("缺少 playwright：.venv\\Scripts\\python.exe -m pip install playwright", file=sys.stderr)
    raise SystemExit(1)

VIEWPORT = {"width": 393, "height": 852}
DEMO_MP4 = ROOT / "core" / "webgrab" / "static" / "demo" / "clip.mp4"
PUB_SHOTS = ROOT / "shots"

# 拦下真实下载，只数点击次数
HOOK = """
window.__dlClicks = 0;
window.__dlUrls = [];
const __orig = HTMLAnchorElement.prototype.click;
HTMLAnchorElement.prototype.click = function () {
  try { window.__dlClicks++; window.__dlUrls.push(this.href); } catch (e) {}
  return undefined;
};
"""


class Failure(Exception):
    pass


# 横向溢出检查：scrollWidth 不能超过视口，可见元素右边缘也不能超出
OVERFLOW_JS = """() => {
  const de = document.documentElement;
  let widest = 0, worst = '';
  document.querySelectorAll('body *').forEach(el => {
    if (el.classList.contains('bg-glow')) return;
    if (getComputedStyle(el).position === 'fixed') return;
    if (el.closest('.hidden')) return;
    const r = el.getBoundingClientRect();
    if (r.width && r.right > widest) {
      widest = r.right;
      worst = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
        (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/)[0] : '');
    }
  });
  return {
    scrollWidth: de.scrollWidth,
    clientWidth: de.clientWidth,
    innerWidth: window.innerWidth,
    widest: Math.round(widest),
    worst: worst
  };
}"""


def check(cond, message: str) -> None:
    if not cond:
        raise Failure(message)
    print(f"  ok · {message}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="WebGrab 移动端 UI 测试")
    parser.add_argument("--base", default="http://127.0.0.1:8765", help="服务地址")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--slow", type=int, default=0, help="每步延时毫秒（调试用）")
    parser.add_argument("--shots", default=".tmp/ui", help="截图目录，传空字符串关闭")
    parser.add_argument("--timeout", type=int, default=90, help="单步超时秒数")
    args = parser.parse_args(argv)

    base = args.base.rstrip("/")
    shot_dir = (ROOT / args.shots) if args.shots else None
    if shot_dir:
        shot_dir.mkdir(parents=True, exist_ok=True)

    print(f"WebGrab UI 测试 · {base}")
    if not DEMO_MP4.is_file():
        print(f"缺少示例视频 {DEMO_MP4}，先跑 tools/make_assets.py", file=sys.stderr)
        return 1

    ms = args.timeout * 1000
    failures = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=not args.headed)
        context = browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            locale="zh-CN",
        )
        context.add_init_script(HOOK)
        page = context.new_page()
        page.set_default_timeout(ms)

        pageerrors = []
        page.on("pageerror", lambda e: pageerrors.append(str(e)))
        api_hits = []
        page.on("request", lambda r: api_hits.append(r.url))

        def shot(name: str) -> None:
            if shot_dir:
                page.screenshot(path=str(shot_dir / f"{name}.png"))

        def slow() -> None:
            if args.slow:
                page.wait_for_timeout(args.slow)

        def run(name, fn) -> None:
            print(f"[{name}]")
            try:
                fn()
            except Failure as exc:
                failures.append(f"{name}: {exc}")
                print(f"  FAIL · {exc}")
                shot("fail-" + name)
            except PWTimeout as exc:
                failures.append(f"{name}: 超时 {exc}")
                print(f"  FAIL · 超时：{str(exc).splitlines()[0]}")
                shot("fail-" + name)
            except Exception as exc:  # noqa: BLE001 - 记录现场继续跑
                failures.append(f"{name}: {exc}")
                print(f"  FAIL · {exc}")
                traceback.print_exc()
                shot("fail-" + name)

        # ---------------------------------------------------------- 1 示例解析
        def case_scan():
            page.goto(base + "/", wait_until="load")
            page.wait_for_selector("#result:not(.hidden)", timeout=ms)
            page.wait_for_function(
                "document.querySelectorAll('.tile').length === 5", timeout=ms
            )
            check(page.locator(".tile").count() == 5, "示例解析出 5 项")
            check("海边黄昏" in page.inner_text("#pageTitle"), "标题来自示例页")
            check("5 项" in page.inner_text("#pageMeta"), "页脚统计为 5 项")
            tabs = page.locator("#tabs .tab")
            check(tabs.count() == 3, "分组 tab：全部/图片/视频")
            check("全部5" in page.inner_text("#tabs").replace("\n", ""), "全部 tab 计数为 5")
            check("已选 0 / 共 5" in page.inner_text("#selCount"), "初始已选 0 / 共 5")
            check(page.locator("#downloadBtn").is_disabled(), "未选时下载按钮禁用")
            check(any("/api/scan_stream" in u for u in api_hits), "优先走 /api/scan_stream（SSE）")
            slow()
            shot("01-scan")

        # ------------------------------------------------------------ 2 预览
        def case_viewer():
            page.locator(".tile .thumb").first.click()
            page.wait_for_selector("#viewer:not(.hidden)")
            check(page.inner_text("#vwCount").replace(" ", "") == "1/5", "预览打开，计数 1/5")
            first_name = page.inner_text("#vwName").strip()
            page.locator("#vwNext").click()
            page.wait_for_function(
                "document.querySelector('#vwCount').textContent.replace(/\\s/g,'') === '2/5'"
            )
            check(page.inner_text("#vwName").strip() != first_name, "左右按钮能切换下一张")
            nav = page.locator("#vwNext").bounding_box() or {"width": 0, "height": 0}
            check(nav["width"] >= 44 and nav["height"] >= 44,
                  f"导航按钮点击区域 {nav['width']:.0f}x{nav['height']:.0f} ≥ 44px")
            # 按钮不能被预览图盖住
            check(page.evaluate(
                "() => { const r = document.querySelector('#vwNext').getBoundingClientRect();"
                " const el = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);"
                " return !!(el && el.closest('#vwNext')); }"
            ), "导航按钮在最上层可点击")

            # 切到第 2 张（图片）后双击放大
            stage_img = page.locator("#vwStage img")
            check(stage_img.count() == 1, "第 2 项是图片")

            def center_of_stage() -> tuple[float, float]:
                box = stage_img.bounding_box() or {"x": 0, "y": 0, "width": 393, "height": 852}
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] / 2
                cx = max(8.0, min(VIEWPORT["width"] - 8.0, cx))
                cy = max(8.0, min(VIEWPORT["height"] - 8.0, cy))
                return cx, cy

            # 放大后的图会超出视口，用坐标双击而不是定位元素
            page.mouse.dblclick(*center_of_stage())
            page.wait_for_selector("#vwStage img.zoom")
            check(page.eval_on_selector("#vwStage img", "e=>e.classList.contains('zoom')"), "双击放大")
            slow()
            shot("02-viewer-zoom")
            page.mouse.dblclick(*center_of_stage())
            page.wait_for_function("!document.querySelector('#vwStage img.zoom')")
            check(True, "再次双击还原")

            page.keyboard.press("Escape")
            page.wait_for_function(
                "document.querySelector('#viewer').classList.contains('hidden')"
            )
            check(page.eval_on_selector("#viewer", "e=>e.classList.contains('hidden')"), "Esc 关闭预览")

        # ------------------------------------------------- 3 选中计数 + 分类全选
        def case_select():
            page.locator(".tile .check").nth(1).click()
            page.wait_for_function(
                "document.querySelector('#selCount').textContent.includes('已选 1 / 共 5')"
            )
            check(page.locator(".tile.sel").count() == 1, "点圆圈选中 1 项")
            check(page.inner_text("#selNum").strip() == "1", "下载按钮上的数字为 1")

            # 切到「图片」分类后全选，只应选中 4 张图
            page.locator("#tabs .tab", has_text="图片").click()
            page.wait_for_function("document.querySelectorAll('.tile').length === 4")
            page.locator("#selAll").click()
            page.wait_for_function(
                "document.querySelector('#selCount').textContent.includes('已选 4 / 共 5')"
            )
            check("已选 4 / 共 5" in page.inner_text("#selCount"), "分类全选只选中当前分类 4 张图")
            slow()
            shot("03-select")

        # ------------------------------------------------------------ 4 批量下载
        def case_download():
            page.locator("#tabs .tab", has_text="全部").click()
            page.wait_for_function("document.querySelectorAll('.tile').length === 5")
            page.locator("#selAll").click()
            page.wait_for_function(
                "document.querySelector('#selCount').textContent.includes('已选 5 / 共 5')"
            )
            check(not page.locator("#downloadBtn").is_disabled(), "选中后下载按钮可用")
            page.locator("#downloadBtn").click()
            page.wait_for_function("window.__dlClicks >= 5", timeout=ms)
            urls = page.evaluate("window.__dlUrls.slice()")
            check(len(urls) >= 5, f"批量下载触发 {len(urls)} 次下载")
            check(all("/api/dl" in u for u in urls[:5]), "都走 /api/dl")
            check(not page.locator("#downloadBtn").is_disabled(), "下载结束后按钮恢复可用")
            slow()
            shot("04-download")

        # ------------------------------------------------------- 5 上传转音频
        def case_convert():
            page.locator("#fmtRow .fmt[data-fmt='mp3']").click()
            page.set_input_files("#videoFile", str(DEMO_MP4))
            page.wait_for_function(
                "document.querySelector('#fileLabel').textContent.includes('clip')"
            )
            check(not page.locator("#convBtn").is_disabled(), "选好文件后转换按钮可用")
            page.locator("#convBtn").click()
            page.wait_for_selector(".conv-result", timeout=ms)
            href = page.get_attribute(".conv-result", "href")
            check(bool(href) and "/api/convert/file?id=" in href, "上传转换出产物链接")
            name = page.inner_text(".conv-result")
            check(".mp3" in name, f"产物是 mp3：{name.strip()}")
            check("is-ok" in (page.get_attribute("#convStatus", "class") or ""), "状态为成功")
            # 产物真的能取到（HEAD/GET 200，且是音频而非 JSON 报错）
            resp = context.request.get(base + href)
            check(resp.ok, f"产物可下载 HTTP {resp.status}")
            check((resp.headers.get("content-type") or "").startswith("audio/"),
                  f"产物 content-type 是音频：{resp.headers.get('content-type')}")
            slow()
            shot("05-convert")

        # ------------------------------------------------------- 6 分享链接进入
        def case_share():
            target = base + "/demo"
            page.goto(base + "/share?url=" + quote(target, safe="") + "&title=" + quote("海边黄昏"),
                      wait_until="load")
            page.wait_for_function(
                "document.querySelectorAll('.tile').length === 5", timeout=ms
            )
            check(page.locator(".tile").count() == 5, "/share 带参自动解析出 5 项")
            check(page.evaluate("location.pathname") == "/", "解析后 replaceState('/')")
            check(target in page.input_value("#urlInput"), "输入框回填分享的 URL")
            slow()
            shot("06-share")

        # --------------------------------------------- 7 不同屏宽都不横向溢出
        def case_overflow():
            for width in (360, 393, 430):
                ctx = browser.new_context(
                    viewport={"width": width, "height": 852},
                    device_scale_factor=2, is_mobile=True, has_touch=True, locale="zh-CN",
                )
                try:
                    pg = ctx.new_page()
                    pg.goto(base + "/", wait_until="load")
                    pg.wait_for_function(
                        "document.querySelectorAll('.tile').length === 5", timeout=ms
                    )
                    pg.wait_for_timeout(300)
                    info = pg.evaluate(OVERFLOW_JS)
                    check(info["scrollWidth"] <= width,
                          f"{width}px 无横向滚动（scrollWidth={info['scrollWidth']}）")
                    check(info["widest"] <= width,
                          f"{width}px 无元素越界（最右 {info['widest']}px {info['worst']}）")
                    if shot_dir:
                        pg.screenshot(path=str(shot_dir / f"07-width-{width}.png"))
                finally:
                    ctx.close()

        # -------------------------------------- 8 布局自解释（帮助弹层 + 无教学卡）
        def case_redesign():
            check(page.locator("#guide").count() == 0, "教学卡已删除")
            check(page.locator("#convToggle").count() == 0 and page.locator("#convBody").count() == 0,
                  "转音频不再折叠，占位常驻")
            check(page.locator("#convertCard").is_visible(), "转音频控件在首屏可见")
            # 帮助弹层：抖音开关 + 接口设置都收在这里
            page.locator("#helpBtn").click()
            page.wait_for_selector("#help:not(.hidden)")
            check(page.locator("#help #tpToggle").count() == 1, "帮助弹层里有抖音换源开关")
            check(page.locator("#help .switch i").is_visible(), "开关外观可见")
            was = page.is_checked("#tpToggle")
            page.locator("#help .switch").click()
            check(page.is_checked("#tpToggle") != was, "点开关能切换第三方解析")
            page.locator("#help .switch").click()
            check(page.locator("#dyApiBtn").is_visible(), "帮助弹层里有抖音接口设置")
            if shot_dir:
                page.screenshot(path=str(shot_dir / "08-help.png"))
            page.keyboard.press("Escape")
            page.wait_for_function("document.querySelector('#help').classList.contains('hidden')")
            check(page.locator("#help").is_hidden(), "Esc 关闭帮助弹层")

            # 交付截图：首屏（只有输入 + 主按钮 + 转音频）与结果页
            PUB_SHOTS.mkdir(parents=True, exist_ok=True)
            page.goto(base + "/", wait_until="domcontentloaded")
            page.add_style_tag(content="#progress,#error,#result,#bar{display:none!important}")
            page.wait_for_selector("#goBtn")
            page.wait_for_timeout(120)
            page.screenshot(path=str(PUB_SHOTS / "frontend-redesign-home.png"))
            page.goto(base + "/", wait_until="load")
            page.wait_for_function(
                "document.querySelectorAll('.tile').length === 5", timeout=ms
            )
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_function("document.querySelector('#progress').classList.contains('hidden')")
            page.wait_for_timeout(200)
            page.screenshot(path=str(PUB_SHOTS / "frontend-redesign-result.png"))
            check((PUB_SHOTS / "frontend-redesign-home.png").is_file()
                  and (PUB_SHOTS / "frontend-redesign-result.png").is_file(),
                  "首屏/结果页截图已写入 shots/")

        # ------------------------------------------ 9 抖音直链带 page/via 重解析参数
        def case_dy_dl():
            url = page.evaluate(
                "(it) => WebGrab.tool.dlUrl(it)",
                {"url": "http://x/y.mp4", "name": "y.mp4", "kind": "video",
                 "via": "dy", "page": "http://p/1?q=2", "source": "douyin"},
            )
            check("via=dy" in url, f"抖音直链带 via=dy：{url}")
            check("page=" + quote("http://p/1?q=2", safe="") in url, "抖音直链带 page=页面地址")
            plain = page.evaluate(
                "(it) => WebGrab.tool.dlUrl(it)",
                {"url": "http://x/a.jpg", "name": "a.jpg", "kind": "image"},
            )
            check("via=" not in plain and "page=" not in plain, f"普通素材参数不变：{plain}")

        run("1 示例解析", case_scan)
        run("2 预览", case_viewer)
        run("3 选中与分类全选", case_select)
        run("4 批量下载", case_download)
        run("5 上传转音频", case_convert)
        run("6 分享链接", case_share)
        run("7 屏宽不溢出", case_overflow)
        run("8 布局自解释", case_redesign)
        run("9 抖音直链参数", case_dy_dl)

        if pageerrors:
            print("[页面异常]")
            for err in pageerrors[:5]:
                print("  JS error:", err)
            failures.append(f"页面出现 {len(pageerrors)} 个 JS 异常")

        browser.close()

    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print("  -", f)
        return 1
    print("全部通过 ✓" + (f"（截图：{shot_dir}）" if shot_dir else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
