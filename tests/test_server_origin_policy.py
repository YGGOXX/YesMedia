"""Request-origin policy tests for the loopback-only local service."""

from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from webgrab import server


@pytest.fixture
def service_url():
    port = server.start(port=0)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.stop()


def request(url, method="GET", headers=None):
    req = Request(url, method=method, headers=headers or {})
    try:
        return urlopen(req, timeout=3)
    except HTTPError as exc:
        return exc


def test_native_request_without_origin_is_allowed(service_url):
    with request(f"{service_url}/api/health") as response:
        assert response.status == 200
        assert b'"ok": true' in response.read()


def test_matching_origin_and_same_site_fetch_are_allowed(service_url):
    with request(
        f"{service_url}/api/health",
        headers={"Origin": service_url, "Sec-Fetch-Site": "same-site"},
    ) as response:
        assert response.status == 200


def test_cross_origin_and_cross_site_requests_are_rejected(service_url):
    response = request(
        f"{service_url}/api/health",
        headers={"Origin": "https://attacker.example"},
    )
    assert response.code == 403

    response = request(
        f"{service_url}/api/health",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert response.code == 403


def test_host_mismatch_is_rejected(service_url):
    response = request(
        f"{service_url}/api/health",
        headers={"Host": "attacker.example"},
    )
    assert response.code == 403


def test_missing_host_is_rejected(service_url):
    from http.client import HTTPConnection
    from urllib.parse import urlsplit

    parsed = urlsplit(service_url)
    conn = HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    conn.putrequest("GET", "/api/health", skip_host=True)
    conn.endheaders()
    response = conn.getresponse()
    assert response.status == 403
    response.read()
    conn.close()


def test_same_origin_options_does_not_grant_cors(service_url):
    with request(
        f"{service_url}/api/config",
        method="OPTIONS",
        headers={"Origin": service_url},
    ) as response:
        assert response.status == 204
        assert response.headers.get("Access-Control-Allow-Origin") is None


def test_post_without_origin_remains_usable_to_native_clients(service_url):
    response = request(
        f"{service_url}/api/config",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    assert response.code == 200
    assert response.headers.get("Access-Control-Allow-Origin") is None


def test_post_from_untrusted_origin_is_rejected(service_url):
    response = request(
        f"{service_url}/api/config",
        method="POST",
        headers={"Origin": "https://attacker.example", "Content-Type": "application/json"},
    )
    assert response.code == 403
