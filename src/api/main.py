from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from config import get_settings
from services.store import persist_result  # noqa: F401  - import side-effect: schema is created on first write


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lead Qualification Agent",
        description=(
            "Inbound webhook that fetches a public company homepage, scores it against a "
            "written ICP rubric, routes the lead, and optionally drafts outreach."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
