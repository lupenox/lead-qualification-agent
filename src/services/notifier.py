from __future__ import annotations

import httpx

from config import Settings, get_settings
from schemas import QualificationResult, RouteDecision


def format_slack_message(result: QualificationResult) -> dict:
    color = {
        RouteDecision.QUALIFY: "#2eb67d",
        RouteDecision.NURTURE: "#ecb22e",
        RouteDecision.REJECT: "#e01e5a",
    }[result.route]
    reasons = "\n".join(f"• {reason}" for reason in result.score.reasons[:5]) or "• n/a"
    return {
        "text": f"{result.route.value.upper()} · {result.company_name} · {result.score.total}/100",
        "attachments": [
            {
                "color": color,
                "fields": [
                    {"title": "Company", "value": result.company_name, "short": True},
                    {"title": "Route", "value": result.route.value, "short": True},
                    {"title": "Score", "value": str(result.score.total), "short": True},
                    {"title": "Website", "value": result.website, "short": True},
                    {"title": "Why", "value": reasons, "short": False},
                ],
            }
        ],
    }


async def notify_slack(
    result: QualificationResult,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    settings = settings or get_settings()
    if not settings.slack_webhook_url:
        return False
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(settings.slack_webhook_url, json=format_slack_message(result))
        response.raise_for_status()
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        if owns_client:
            await client.aclose()
