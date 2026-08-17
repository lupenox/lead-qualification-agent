from __future__ import annotations

from fastapi import APIRouter, HTTPException

from engine.graph import run_qualification
from schemas import InboundLead, QualificationResult
from services.store import get_lead, list_leads

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/webhook/inbound-lead", response_model=QualificationResult)
async def inbound_lead(lead: InboundLead) -> QualificationResult:
    return await run_qualification(lead)


@router.get("/leads")
async def leads(limit: int = 50) -> list[dict]:
    return list_leads(limit=limit)


@router.get("/leads/{lead_id}")
async def lead_detail(lead_id: int) -> dict:
    record = get_lead(lead_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Lead not found")
    return record
