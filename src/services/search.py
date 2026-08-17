from __future__ import annotations

import json
from urllib.parse import quote_plus

import httpx

from config import Settings, get_settings


async def enrich_company(
    company_name: str,
    website: str,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Optional public-web snippets. Tavily if keyed, otherwise DuckDuckGo HTML."""
    settings = settings or get_settings()
    query = f"{company_name} official site {website}"
    if settings.tavily_api_key:
        snippets = await _tavily_search(query, settings)
        if snippets:
            return snippets
    return await _duckduckgo_search(query, settings, client)


async def _tavily_search(query: str, settings: Settings) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": query,
                    "max_results": 3,
                    "include_answer": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
            return [
                f"{item.get('title', '')}: {item.get('content', '')}".strip(": ")
                for item in payload.get("results", [])[:3]
            ]
    except Exception:  # noqa: BLE001
        return []


async def _duckduckgo_search(
    query: str,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {"User-Agent": settings.user_agent}
    owns_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=12.0)
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(response.text, "html.parser")
        snippets: list[str] = []
        for result in soup.select(".result__snippet")[:3]:
            text = result.get_text(" ", strip=True)
            if text:
                snippets.append(text)
        return snippets
    except Exception:  # noqa: BLE001
        return []
    finally:
        if owns_client:
            await client.aclose()


def dump_snippets(snippets: list[str]) -> str:
    return json.dumps(snippets, indent=2)
