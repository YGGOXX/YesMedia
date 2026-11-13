"""抖音桥多 item（视频/图集/LivePhoto/错误码）回归测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from webgrab import engines, extract  # noqa: E402

PAGE = "https://www.douyin.com/note/7412345678901234567"


def _native_payload(items):
    return {
        "ok": True,
        "platform": "douyin",
        "source": "native",
        "resolved_url": PAGE,
        "aweme_id": "7412345678901234567",
        "title": "图集标题",
        "author": "作者",
        "cover": "https://p3.douyinpic.com/cover.jpg",
        "referer": "https://www.douyin.com/",
        "media_type": "gallery",
        "items": items,
    }


def test_gallery_items_are_kept(monkeypatch):
    payload = _native_payload(
        [
            {"kind": "image", "url": "https://p3.douyinpic.com/a.jpg?x=1", "width": 1320, "height": 1750},
            {
                "kind": "image",
                "url": "https://p3.douyinpic.com/b.webp",
                "width": 1320,
                "height": 1750,
                "live_photo": {"url": "https://v3.douyinvod.com/live.mp4"},
            },
        ]
    )
    monkeypatch.setattr(engines, "_android_bridge_resolve", lambda *a, **k: payload)
    result = engines.resolve_douyin(PAGE, use_cache=False)
    assert result["engine"] == "douyin"
    assert len(result["items"]) == 2
    first, second = result["items"]
    assert first["kind"] == "image"
    assert first["via"] == "dy"
    assert first["page"] == PAGE
    assert first["ext"] == "jpg"
    assert second["ext"] == "webp"
    assert second["live_url"] == "https://v3.douyinvod.com/live.mp4"

    title, items, groups = extract._engine_items(result, PAGE, PAGE)
    assert len(items) == 2
    assert groups["image"] == items
    assert groups["video"] == []
    assert all(it["kind"] == "image" for it in items)
    assert items[0]["name"].endswith(".jpg")
    assert items[1]["name"].endswith(".webp")


def test_video_plus_gallery_keeps_all(monkeypatch):
    payload = _native_payload(
        [
            {
                "kind": "video",
                "url": "https://v11-weba.douyinvod.com/v.mp4",
                "width": 720,
                "height": 1280,
                "size": 12345,
                "watermark": True,
            },
            {"kind": "image", "url": "https://p3.douyinpic.com/a.jpg"},
        ]
    )
    monkeypatch.setattr(engines, "_android_bridge_resolve", lambda *a, **k: payload)
    result = engines.resolve_douyin(PAGE, use_cache=False)
    kinds = [it["kind"] for it in result["items"]]
    assert kinds == ["video", "image"]
    video = result["items"][0]
    assert video["w"] == 720
    assert video["h"] == 1280
    assert video["size"] == 12345
    assert video["watermark"] is True
    assert video["name"].endswith(".mp4")


def test_legacy_single_video_payload_still_works(monkeypatch):
    payload = {
        "ok": True,
        "video_url": "https://v11-weba.douyinvod.com/old.mp4",
        "title": "旧桥视频",
        "cover": "https://p3.douyinpic.com/c.jpg",
        "referer": "https://www.douyin.com/",
    }
    monkeypatch.setattr(engines, "_android_bridge_resolve", lambda *a, **k: payload)
    result = engines.resolve_douyin(
        "https://www.douyin.com/video/7412345678901234567", use_cache=False
    )
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["kind"] == "video"
    assert item["url"] == "https://v11-weba.douyinvod.com/old.mp4"
    assert item["name"].endswith(".mp4")


def test_empty_items_are_treated_as_failure(monkeypatch):
    monkeypatch.setattr(
        engines,
        "_android_bridge_resolve",
        lambda *a, **k: {
            "ok": True,
            "items": [],
            "video_url": "",
        },
    )
    monkeypatch.setattr(engines, "douyin_direct", lambda url: None)
    monkeypatch.setattr(
        engines,
        "ytdlp_resolve",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")),
    )
    monkeypatch.setattr(
        engines,
        "douyin_api",
        lambda *a, **k: (_ for _ in ()).throw(engines.EngineError("接口超时")),
    )
    with pytest.raises(engines.EngineError) as info:
        engines.resolve_douyin(PAGE, cfg={"douyin_apis": []}, use_cache=False)
    assert "抖音解析失败" in str(info.value)


def test_bridge_error_code_is_propagated(monkeypatch):
    def fake_bridge(page_url, timeout_ms=25000):
        engines._set_bridge_reason("作品不存在、已删除或不可访问")
        engines._set_bridge_code("UNAVAILABLE")
        return None

    monkeypatch.setattr(engines, "_android_bridge_resolve", fake_bridge)
    monkeypatch.setattr(engines, "douyin_direct", lambda url: None)
    monkeypatch.setattr(
        engines,
        "ytdlp_resolve",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")),
    )
    monkeypatch.setattr(
        engines,
        "douyin_api",
        lambda *a, **k: (_ for _ in ()).throw(engines.EngineError("接口超时")),
    )
    with pytest.raises(engines.EngineError) as info:
        engines.resolve_douyin(PAGE, cfg={"douyin_apis": []}, use_cache=False)
    assert info.value.code == "UNAVAILABLE"


def test_scan_page_douyin_error_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(extract.net, "http_get", lambda *a, **k: (200, b"<html></html>", PAGE))
    monkeypatch.setattr(
        engines,
        "resolve_douyin",
        lambda *a, **k: (_ for _ in ()).throw(engines.EngineError("需要验证", code="NEEDS_VERIFICATION")),
    )
    with pytest.raises(extract.ScanError) as info:
        extract.scan_page(PAGE)
    assert info.value.code == "NEEDS_VERIFICATION"
    assert "需要验证" in str(info.value)


def test_scan_page_keeps_douyin_gallery(monkeypatch):
    monkeypatch.setattr(
        extract.net, "http_get", lambda *a, **k: (200, b"<html><title>t</title></html>", PAGE)
    )
    monkeypatch.setattr(
        engines,
        "resolve_douyin",
        lambda *a, **k: {
            "title": "三张图",
            "engine": "douyin",
            "note": "抖音原生解析",
            "items": [
                {"kind": "image", "url": "https://p3.douyinpic.com/1.jpg", "source": "douyin", "page": PAGE},
                {"kind": "image", "url": "https://p3.douyinpic.com/2.jpg", "source": "douyin", "page": PAGE},
                {"kind": "image", "url": "https://p3.douyinpic.com/3.jpg", "source": "douyin", "page": PAGE},
            ],
        },
    )
    result = extract.scan_page(PAGE)
    assert result["ok"] is True
    assert len(result["items"]) == 3
    assert all(item["kind"] == "image" for item in result["items"])
    assert result["groups"]["image"] == result["items"]
    assert all(item["via"] == "dy" for item in result["items"])
