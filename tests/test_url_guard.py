import asyncio
import ipaddress

import httpx
import pytest

from config import Settings
from schemas import InboundLead
from services.scraper import guarded_get, scrape_homepage
from services.url_guard import UnsafeURLError, is_public_ip, validate_public_url

PUBLIC_IP = "93.184.215.14"


def run(coro):
    return asyncio.run(coro)


def fake_resolver(mapping: dict[str, list[str]]):
    async def resolve(host: str, port: int) -> list[str]:
        if host not in mapping:
            raise OSError(f"no such host {host}")
        return mapping[host]

    return resolve


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.10.0.5",
        "10.0.0.1",
        "172.16.3.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "255.255.255.255",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd12:3456::1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "64:ff9b::a9fe:a9fe",  # NAT64 form of 169.254.169.254
        "2002:a9fe:a9fe::1",  # 6to4 form of 169.254.169.254
        "ff02::1",
    ],
)
def test_non_public_addresses_are_rejected(address):
    assert is_public_ip(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize("address", [PUBLIC_IP, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert is_public_ip(ipaddress.ip_address(address)) is True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:8765/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://0x7f000001/",  # hex-encoded loopback; resolves via the resolver below
        "ftp://example.com/",
        "file:///etc/passwd",
        "http://user:pass@example.com/",
    ],
)
def test_validate_rejects_unsafe_urls(url):
    resolver = fake_resolver({"0x7f000001": ["127.0.0.1"]})
    with pytest.raises(UnsafeURLError):
        run(validate_public_url(url, resolver=resolver))


def test_validate_rejects_hostname_resolving_to_private_ip():
    resolver = fake_resolver({"internal.example": ["10.1.2.3"]})
    with pytest.raises(UnsafeURLError, match="non-public"):
        run(validate_public_url("https://internal.example/", resolver=resolver))


def test_validate_rejects_when_any_resolved_address_is_private():
    resolver = fake_resolver({"mixed.example": [PUBLIC_IP, "127.0.0.1"]})
    with pytest.raises(UnsafeURLError):
        run(validate_public_url("https://mixed.example/", resolver=resolver))


def test_validate_rejects_unresolvable_host():
    with pytest.raises(UnsafeURLError, match="could not resolve"):
        run(validate_public_url("https://nope.example/", resolver=fake_resolver({})))


def test_validate_allows_public_hostname():
    resolver = fake_resolver({"northline.example": [PUBLIC_IP]})
    run(validate_public_url("https://northline.example/", resolver=resolver))


def test_allow_private_flag_skips_ip_checks_but_not_scheme():
    run(validate_public_url("http://127.0.0.1/", allow_private=True))
    with pytest.raises(UnsafeURLError):
        run(validate_public_url("file:///etc/passwd", allow_private=True))


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_guarded_get_revalidates_each_redirect_hop():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

    resolver = fake_resolver({"northline.example": [PUBLIC_IP]})

    async def go():
        async with _client(handler) as client:
            await guarded_get(
                client, "https://northline.example/", headers={}, max_bytes=1000, resolver=resolver
            )

    with pytest.raises(UnsafeURLError, match="169.254.169.254"):
        run(go())
    assert requested == ["https://northline.example/"]  # metadata endpoint never requested


def test_guarded_get_follows_safe_redirects_and_caps_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(301, headers={"Location": "/home"})
        return httpx.Response(200, text="x" * 5000, headers={"Content-Type": "text/html"})

    resolver = fake_resolver({"northline.example": [PUBLIC_IP]})

    async def go():
        async with _client(handler) as client:
            return await guarded_get(
                client, "https://northline.example/", headers={}, max_bytes=100, resolver=resolver
            )

    status, final_url, text = run(go())
    assert status == 200
    assert final_url == "https://northline.example/home"
    assert len(text) == 100


def test_guarded_get_limits_redirect_count():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "/again"})

    resolver = fake_resolver({"northline.example": [PUBLIC_IP]})

    async def go():
        async with _client(handler) as client:
            await guarded_get(
                client, "https://northline.example/", headers={}, max_bytes=100, resolver=resolver
            )

    with pytest.raises(Exception, match="too many redirects"):
        run(go())


def test_scrape_homepage_refuses_loopback_without_any_request():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, text="<title>INTERNAL-ADMIN-SECRET</title>")

    lead = InboundLead(company_name="x", website="http://127.0.0.1:8765/")

    async def go():
        async with _client(handler) as client:
            return await scrape_homepage(lead, settings=Settings(_env_file=None), client=client)

    profile = run(go())
    assert profile.scrape_ok is False
    assert "non-public" in (profile.scrape_error or "")
    assert "INTERNAL-ADMIN-SECRET" not in profile.page_text
    assert requested == []


def test_scrape_homepage_happy_path_with_public_host():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        return httpx.Response(
            200,
            text="<html><title>Northline</title><body>Pricing. SOC 2.</body></html>",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )

    lead = InboundLead(company_name="Northline", website="https://northline.example/")
    resolver = fake_resolver({"northline.example": [PUBLIC_IP]})

    async def go():
        async with _client(handler) as client:
            return await scrape_homepage(
                lead, settings=Settings(_env_file=None), client=client, resolver=resolver
            )

    profile = run(go())
    assert profile.scrape_ok is True, profile.scrape_error
    assert profile.title == "Northline"
    assert "pricing_page" in profile.buying_signals


def test_scrape_homepage_respects_robots_disallow():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        raise AssertionError("homepage must not be fetched when robots.txt disallows it")

    lead = InboundLead(company_name="Northline", website="https://northline.example/")
    resolver = fake_resolver({"northline.example": [PUBLIC_IP]})

    async def go():
        async with _client(handler) as client:
            return await scrape_homepage(
                lead, settings=Settings(_env_file=None), client=client, resolver=resolver
            )

    profile = run(go())
    assert profile.scrape_ok is False
    assert profile.scrape_error == "robots.txt disallows this path"
