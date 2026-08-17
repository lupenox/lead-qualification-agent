from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from config import Settings, get_settings
from engine.ollama_client import get_structured_completion
from engine.prompts import DRAFT_SYSTEM_PROMPT
from engine.scoring import score_lead
from schemas import (
    AgentState,
    InboundLead,
    OutreachDraft,
    QualificationResult,
    RouteDecision,
)
from services.notifier import notify_slack
from services.scraper import scrape_homepage
from services.search import enrich_company
from services.store import persist_result


class GraphState(TypedDict, total=False):
    lead: dict[str, Any]
    profile: dict[str, Any]
    score: dict[str, Any]
    route: str
    draft: dict[str, Any] | None
    notified: bool
    lead_id: int | None
    errors: list[str]


def _settings() -> Settings:
    return get_settings()


async def scrape_node(state: GraphState) -> GraphState:
    lead = InboundLead.model_validate(state["lead"])
    profile = await scrape_homepage(lead)
    errors = list(state.get("errors") or [])
    if not profile.scrape_ok and profile.scrape_error:
        errors.append(profile.scrape_error)
    return {"profile": profile.model_dump(mode="json"), "errors": errors}


async def enrich_node(state: GraphState) -> GraphState:
    lead = InboundLead.model_validate(state["lead"])
    profile_data = dict(state.get("profile") or {})
    snippets = await enrich_company(lead.company_name, str(lead.website))
    profile_data["search_snippets"] = snippets
    return {"profile": profile_data}


def score_node(state: GraphState) -> GraphState:
    lead = InboundLead.model_validate(state["lead"])
    from schemas import CompanyProfile

    profile = CompanyProfile.model_validate(state["profile"])
    score = score_lead(lead, profile)
    return {"score": score.model_dump()}


def route_node(state: GraphState) -> GraphState:
    settings = _settings()
    total = int(state["score"]["total"])
    if total >= settings.qualify_threshold:
        route = RouteDecision.QUALIFY
    elif total >= settings.nurture_threshold:
        route = RouteDecision.NURTURE
    else:
        route = RouteDecision.REJECT
    return {"route": route.value}


def draft_node(state: GraphState) -> GraphState:
    if state.get("route") != RouteDecision.QUALIFY.value:
        return {"draft": None}
    lead = InboundLead.model_validate(state["lead"])
    prompt = json.dumps(
        {
            "company_name": lead.company_name,
            "website": str(lead.website),
            "contact_name": lead.contact_name,
            "score": state["score"],
            "title": state["profile"].get("title"),
            "description": state["profile"].get("description"),
            "headings": state["profile"].get("headings", [])[:8],
            "buying_signals": state["profile"].get("buying_signals", []),
        },
        indent=2,
    )
    try:
        draft = get_structured_completion(prompt, OutreachDraft, DRAFT_SYSTEM_PROMPT)
        return {"draft": draft.model_dump()}
    except Exception:
        first_signal = (state["profile"].get("buying_signals") or ["your public product page"])[0]
        draft = OutreachDraft(
            subject=f"Quick idea for {lead.company_name}",
            body=(
                f"Hi {lead.contact_name or 'there'},\n\n"
                f"I looked at {lead.company_name}'s public site and noticed {first_signal.replace('_', ' ')}. "
                "We help teams turn inbound interest into a scored, routed pipeline with a written ICP rubric "
                "instead of a black-box agent.\n\n"
                "Open to a 15-minute walkthrough this week?"
            ),
            call_to_action="Book a 15-minute scoping call",
        )
        return {"draft": draft.model_dump()}


async def persist_and_notify_node(state: GraphState) -> GraphState:
    agent = AgentState.model_validate(
        {
            "lead": state["lead"],
            "profile": state.get("profile"),
            "score": state.get("score"),
            "route": state.get("route"),
            "draft": state.get("draft"),
            "errors": state.get("errors") or [],
        }
    )
    result = QualificationResult(
        company_name=agent.lead.company_name,
        website=str(agent.lead.website),
        route=agent.route or RouteDecision.REJECT,
        score=agent.score,  # type: ignore[arg-type]
        profile=agent.profile,  # type: ignore[arg-type]
        draft=agent.draft,
    )
    lead_id = persist_result(result)
    result.lead_id = lead_id
    notified = await notify_slack(result)
    return {"lead_id": lead_id, "notified": notified}


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("scrape", scrape_node)
    graph.add_node("enrich", enrich_node)
    graph.add_node("score", score_node)
    graph.add_node("route", route_node)
    graph.add_node("draft", draft_node)
    graph.add_node("persist", persist_and_notify_node)
    graph.add_edge(START, "scrape")
    graph.add_edge("scrape", "enrich")
    graph.add_edge("enrich", "score")
    graph.add_edge("score", "route")
    graph.add_edge("route", "draft")
    graph.add_edge("draft", "persist")
    graph.add_edge("persist", END)
    return graph.compile()


_APP = None


def get_app():
    global _APP
    if _APP is None:
        _APP = build_graph()
    return _APP


async def run_qualification(lead: InboundLead) -> QualificationResult:
    final_state = await get_app().ainvoke({"lead": lead.model_dump(mode="json"), "errors": []})
    return QualificationResult(
        lead_id=final_state.get("lead_id"),
        company_name=lead.company_name,
        website=str(lead.website),
        route=RouteDecision(final_state["route"]),
        score=final_state["score"],
        profile=final_state["profile"],
        draft=final_state.get("draft"),
        notified=bool(final_state.get("notified")),
    )
