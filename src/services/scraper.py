from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config import Settings, get_settings
from schemas import CompanyProfile, InboundLead
from services.robots import robots_allows
from services.url_guard import Resolver, UnsafeURLError, validate_public_url

MAX_REDIRECTS = 5
ROBOTS_MAX_BYTES = 64_000

BUYING_SIGNAL_PATTERNS = {
    "pricing_page": re.compile(r"\b(pricing|plans|request a demo|book a demo)\b", re.I),
    "careers": re.compile(r"\b(careers|we.?re hiring|open roles)\b", re.I),
    "security": re.compile(r"\b(soc 2|hipaa|gdpr|iso 27001)\b", re.I),
    "integration": re.compile(r"\b(api|integrations|webhooks|salesforce|hubspot)\b", re.I),
}

TECH_PATTERNS = {
    "aws": re.compile(r"\baws\b", re.I),
    "azure": re.compile(r"\bazure\b", re.I),
    "gcp": re.compile(r"\b(gcp|google cloud)\b", re.I),
    "salesforce": re.compile(r"\bsalesforce\b", re.I),
    "hubspot": re.compile(r"\bhubspot\b", re.I),
    "shopify": re.compile(r"\bshopify\b", re.I),
}

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _robots_allows(robots_txt: str, user_agent: str, path: str) -> bool:
    """RFC 9309 robots.txt check. Defaults to allow when the file is missing or empty."""
    return robots_allows(robots_txt, user_agent, path)


class FetchError(Exception):
    """Raised when a guarded fetch cannot complete."""


async def guarded_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    max_bytes: int,
    max_redirects: int = MAX_REDIRECTS,
    resolver: Resolver | None = None,
    allow_private: bool = False,
) -> tuple[int, str, str]:
    """GET ``url`` validating every hop against the SSRF guard.

    Redirects are followed manually so each ``Location`` is re-validated before it
    is requested. The body is streamed and truncated at ``max_bytes``.
    Returns ``(status_code, final_url, text)``.
    """
    current = url
    for _ in range(max_redirects + 1):
        await validate_public_url(current, resolver=resolver, allow_private=allow_private)
        request = client.build_request("GET", current, headers=headers)
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(f"redirect from {current} without Location header")
                current = urljoin(current, location)
                continue
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= max_bytes:
                    break
            body = b"".join(chunks)[:max_bytes]
            encoding = response.encoding or "utf-8"
            return response.status_code, current, body.decode(encoding, errors="replace")
        finally:
            await response.aclose()
    raise FetchError(f"too many redirects (>{max_redirects})")


def extract_profile(html: str, lead: InboundLead) -> CompanyProfile:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = (soup.title.string or "").strip() if soup.title else ""
    description = ""
    meta = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    if meta and meta.get("content"):
        description = meta["content"].strip()

    headings = [
        h.get_text(" ", strip=True)
        for h in soup.find_all(["h1", "h2", "h3"])
        if h.get_text(strip=True)
    ][:20]
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:8_000]
    haystack = f"{title} {description} {' '.join(headings)} {page_text}"

    buying_signals = [
        name for name, pattern in BUYING_SIGNAL_PATTERNS.items() if pattern.search(haystack)
    ]
    technologies = [name for name, pattern in TECH_PATTERNS.items() if pattern.search(haystack)]
    emails = sorted(set(EMAIL_RE.findall(page_text)))[:8]

    return CompanyProfile(
        company_name=lead.company_name,
        website=str(lead.website),
        title=title,
        description=description,
        headings=headings,
        page_text=page_text,
        emails=emails,
        technologies=technologies,
        buying_signals=buying_signals,
        scrape_ok=True,
    )


async def scrape_homepage(
    lead: InboundLead,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    resolver: Resolver | None = None,
) -> CompanyProfile:
    settings = settings or get_settings()
    url = str(lead.website)
    headers = {"User-Agent": settings.user_agent, "Accept": "text/html,application/xhtml+xml"}
    fetch_kwargs = {
        "headers": headers,
        "resolver": resolver,
        "allow_private": settings.scrape_allow_private_hosts,
    }
    owns_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=False, timeout=settings.scrape_timeout_seconds)
    try:
        robots_url = urljoin(_origin(url), "/robots.txt")
        try:
            robots_status, _, robots_text = await guarded_get(
                client, robots_url, max_bytes=ROBOTS_MAX_BYTES, **fetch_kwargs
            )
        except UnsafeURLError:
            raise
        except Exception:  # noqa: BLE001 - unreachable robots.txt means "no rules"
            robots_status, robots_text = 0, ""
        parsed = urlparse(url)
        path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        if robots_status == 200 and not _robots_allows(robots_text, settings.user_agent, path):
            return CompanyProfile(
                company_name=lead.company_name,
                website=url,
                scrape_ok=False,
                scrape_error="robots.txt disallows this path",
            )

        status, final_url, html = await guarded_get(
            client, url, max_bytes=settings.scrape_max_bytes, **fetch_kwargs
        )
        if status >= 400:
            raise FetchError(f"HTTP {status} for {final_url}")
        return extract_profile(html, lead)
    except Exception as exc:  # noqa: BLE001 - persist a usable profile on any fetch failure
        return CompanyProfile(
            company_name=lead.company_name,
            website=url,
            scrape_ok=False,
            scrape_error=str(exc),
        )
    finally:
        if owns_client:
            await client.aclose()
