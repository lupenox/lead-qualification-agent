from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from config import Settings, get_settings
from engine.scoring import heuristic_score
from schemas import CompanyProfile, InboundLead, RouteDecision


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    get_settings.cache_clear()
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("QUALIFY_THRESHOLD", "75")
    monkeypatch.setenv("NURTURE_THRESHOLD", "50")
    yield Settings()
    get_settings.cache_clear()


def test_heuristic_score_rewards_buying_signals(settings: Settings):
    lead = InboundLead(
        company_name="Northline Logistics",
        website="https://northline.example",
        claimed_employees=80,
        claimed_country="United States",
        contact_email="ops@northline.example",
    )
    profile = CompanyProfile(
        company_name=lead.company_name,
        website=str(lead.website),
        title="Freight OS",
        description="SaaS for freight brokers",
        headings=["Pricing", "Integrations"],
        page_text="Book a demo. SOC 2. HubSpot.",
        buying_signals=["pricing_page", "security", "integration"],
        technologies=["hubspot"],
        scrape_ok=True,
    )
    score = heuristic_score(lead, profile, settings)
    assert score.total >= 75
    assert score.scorer == "heuristic"
    assert any("buying signals" in reason.lower() for reason in score.reasons)


def test_low_signal_lead_does_not_auto_qualify(settings: Settings):
    lead = InboundLead(company_name="Unknown Co", website="https://unknown.example")
    profile = CompanyProfile(
        company_name="Unknown Co",
        website="https://unknown.example",
        scrape_ok=False,
        scrape_error="timeout",
    )
    score = heuristic_score(lead, profile, settings)
    assert score.total < 75


@pytest.mark.asyncio
async def test_full_graph_with_offline_scrape(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    from engine import graph as graph_mod
    from schemas import CompanyProfile, OutreachDraft

    async def fake_scrape(lead, settings=None, client=None):
        return CompanyProfile(
            company_name=lead.company_name,
            website=str(lead.website),
            title="Acme SaaS",
            description="B2B software platform",
            headings=["Pricing", "Security"],
            page_text="Request a demo. SOC 2. Salesforce.",
            buying_signals=["pricing_page", "security", "integration"],
            technologies=["salesforce"],
            scrape_ok=True,
        )

    async def fake_enrich(company_name, website, settings=None, client=None):
        return ["Acme is a US software company."]

    async def fake_notify(result, settings=None, client=None):
        return False

    monkeypatch.setattr(graph_mod, "scrape_homepage", fake_scrape)
    monkeypatch.setattr(graph_mod, "enrich_company", fake_enrich)
    monkeypatch.setattr(graph_mod, "notify_slack", fake_notify)
    monkeypatch.setattr(
        graph_mod,
        "get_structured_completion",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    graph_mod._APP = None

    lead = InboundLead(
        company_name="Acme",
        website="https://acme.example",
        claimed_employees=40,
        claimed_country="United States",
        contact_name="Ada",
        contact_email="ada@acme.example",
    )
    result = await graph_mod.run_qualification(lead)
    assert result.route == RouteDecision.QUALIFY
    assert result.draft is not None
    assert result.lead_id is not None
    assert isinstance(result.draft, OutreachDraft) or result.draft["subject"]


def test_health_endpoint():
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
