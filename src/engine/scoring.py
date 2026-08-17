from __future__ import annotations

import json
import re

from config import Settings, get_settings
from engine.ollama_client import get_structured_completion
from engine.prompts import QUALIFY_SYSTEM_PROMPT
from schemas import CompanyProfile, ICPScore, InboundLead, ScoreBreakdown

US_CA_UK_EU = re.compile(
    r"\b(united states|usa|u\.s\.|canada|united kingdom|uk|ireland|germany|france|netherlands|eu|europe)\b",
    re.I,
)


class _LLMScore(ICPScore):
    pass


def heuristic_score(lead: InboundLead, profile: CompanyProfile, settings: Settings | None = None) -> ICPScore:
    settings = settings or get_settings()
    text = " ".join(
        [
            profile.title,
            profile.description,
            " ".join(profile.headings),
            profile.page_text,
            " ".join(profile.search_snippets),
            lead.notes or "",
        ]
    ).lower()

    size = 8
    if lead.claimed_employees:
        if 10 <= lead.claimed_employees <= 500:
            size = 20
        elif 5 <= lead.claimed_employees < 10 or 501 <= lead.claimed_employees <= 2000:
            size = 12
        else:
            size = 6

    industry = 6
    for name in settings.target_industries:
        if name in text or name in lead.company_name.lower():
            industry = 18
            break
    if any(word in text for word in ("software", "platform", "saas", "api")):
        industry = max(industry, 16)

    geo = 7
    country = (lead.claimed_country or "") + " " + text
    if US_CA_UK_EU.search(country):
        geo = 14

    buying = 4 * len(set(profile.buying_signals))
    buying = min(25, buying + (4 if profile.technologies else 0))

    completeness = 6
    if profile.scrape_ok:
        completeness += 8
    if lead.contact_email or profile.emails:
        completeness += 3
    if profile.description or profile.headings:
        completeness += 3
    completeness = min(20, completeness)

    breakdown = ScoreBreakdown(
        company_size_fit=size,
        industry_fit=industry,
        geography_fit=geo,
        buying_signal=buying,
        data_completeness=completeness,
    )
    reasons = []
    if profile.buying_signals:
        reasons.append(f"Detected buying signals: {', '.join(profile.buying_signals)}")
    if profile.technologies:
        reasons.append(f"Mentioned stack: {', '.join(profile.technologies)}")
    if profile.title:
        reasons.append(f"Homepage title: {profile.title}")
    if not reasons:
        reasons.append("Scored from limited public homepage text only")
    risks = []
    if not profile.scrape_ok:
        risks.append(profile.scrape_error or "Homepage could not be fetched")
    if not lead.claimed_employees:
        risks.append("Employee count was not provided")
    return ICPScore(total=breakdown.total, breakdown=breakdown, reasons=reasons, risks=risks, scorer="heuristic")


def score_lead(lead: InboundLead, profile: CompanyProfile, settings: Settings | None = None) -> ICPScore:
    settings = settings or get_settings()
    fallback = heuristic_score(lead, profile, settings)
    prompt = json.dumps(
        {
            "lead": lead.model_dump(mode="json"),
            "profile": profile.model_dump(mode="json"),
            "heuristic_suggestion": fallback.model_dump(),
            "instruction": "Adjust the heuristic only when the page text clearly supports a change.",
        },
        indent=2,
    )
    try:
        scored = get_structured_completion(prompt, _LLMScore, QUALIFY_SYSTEM_PROMPT, settings)
        scored.scorer = settings.llm_provider
        scored.total = scored.breakdown.total
        return scored
    except Exception:
        return fallback
