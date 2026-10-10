from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import router as auth_router
from app.agent.router import router as agent_router
from app.core.config import get_settings
from app.db import check_database
from app.github.client import GitHubAPIError, GitHubClient
from app.github.oauth import router as github_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"],
)
app.include_router(auth_router)
app.include_router(github_router)
app.include_router(agent_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["health"])
def readiness() -> dict[str, str]:
    try:
        check_database()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is unavailable") from exc
    return {"status": "ready", "database": "ok"}


def github_client() -> GitHubClient:
    return GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)


async def list_github_repositories(access_token: str):
    """Integration seam for an authenticated account flow; no client route until scoped auth exists."""
    try:
        return await github_client().list_repositories(access_token)
    except GitHubAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
