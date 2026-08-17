from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


class RouteDecision(str, Enum):
    QUALIFY = "qualify"
    NURTURE = "nurture"
    REJECT = "reject"


class InboundLead(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=200)
    website: HttpUrl
    contact_name: str | None = None
    contact_email: str | None = None
    source: str = "manual"
    notes: str | None = None
    claimed_employees: int | None = Field(default=None, ge=1)
    claimed_country: str | None = None


class CompanyProfile(BaseModel):
    company_name: str
    website: str
    title: str = ""
    description: str = ""
    headings: list[str] = Field(default_factory=list)
    page_text: str = ""
    emails: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    buying_signals: list[str] = Field(default_factory=list)
    search_snippets: list[str] = Field(default_factory=list)
    scrape_ok: bool = True
    scrape_error: str | None = None


class ScoreBreakdown(BaseModel):
    company_size_fit: int = Field(ge=0, le=20)
    industry_fit: int = Field(ge=0, le=20)
    geography_fit: int = Field(ge=0, le=15)
    buying_signal: int = Field(ge=0, le=25)
    data_completeness: int = Field(ge=0, le=20)

    @property
    def total(self) -> int:
        return (
            self.company_size_fit
            + self.industry_fit
            + self.geography_fit
            + self.buying_signal
            + self.data_completeness
        )


class ICPScore(BaseModel):
    total: int = Field(ge=0, le=100)
    breakdown: ScoreBreakdown
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    scorer: str = "heuristic"

    @field_validator("total")
    @classmethod
    def total_matches_breakdown(cls, value: int, info) -> int:
        breakdown = info.data.get("breakdown")
        if breakdown is not None and value != breakdown.total:
            return breakdown.total
        return value


class OutreachDraft(BaseModel):
    subject: str
    body: str
    call_to_action: str


class QualificationResult(BaseModel):
    lead_id: int | None = None
    company_name: str
    website: str
    route: RouteDecision
    score: ICPScore
    profile: CompanyProfile
    draft: OutreachDraft | None = None
    notified: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentState(BaseModel):
    lead: InboundLead
    profile: CompanyProfile | None = None
    score: ICPScore | None = None
    route: RouteDecision | None = None
    draft: OutreachDraft | None = None
    notified: bool = False
    lead_id: int | None = None
    errors: list[str] = Field(default_factory=list)

    def to_graph_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
