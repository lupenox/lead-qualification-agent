from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config import Settings, get_settings
from schemas import CompanyProfile, InboundLead

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
    """Minimal robots.txt check. Defaults to allow when the file is missing or unreadable."""
    if not robots_txt.strip():
        return True
    applicable = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("user-agent:"):
            agent = line.split(":", 1)[1].strip()
            applicable = agent == "*" or agent.lower() in user_agent.lower()
            continue
        if applicable and line.lower().startswith("disallow:"):
            rule = line.split(":", 1)[1].strip() or "/"
            if path.startswith(rule):
                return False
    return True


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
) -> CompanyProfile:
    settings = settings or get_settings()
    url = str(lead.website)
    headers = {"User-Agent": settings.user_agent, "Accept": "text/html,application/xhtml+xml"}
    owns_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=settings.scrape_timeout_seconds)
    try:
        robots_url = urljoin(_origin(url), "/robots.txt")
        robots_resp = await client.get(robots_url, headers=headers)
        path = urlparse(url).path or "/"
        if robots_resp.status_code == 200 and not _robots_allows(
            robots_resp.text, settings.user_agent, path
        ):
            return CompanyProfile(
                company_name=lead.company_name,
                website=url,
                scrape_ok=False,
                scrape_error="robots.txt disallows this path",
            )

        response = await client.get(url, headers=headers)
        response.raise_for_status()
        html = response.text[: settings.scrape_max_bytes]
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
