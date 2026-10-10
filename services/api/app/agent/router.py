from __future__ import annotations

import json
import re
from datetime import timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_session, enforce_auth_rate_limit, require_csrf
from app.core.config import get_settings
from app.db import get_db
from app.github.client import GitHubAPIError, GitHubClient
from app.github.oauth import fresh_access_token, require_configuration, selected_account
from app.models import AgentTask, AgentTaskEvent, SessionRecord, User, Workspace
from app.agent.planner import PlanGenerationError, generate_plan

router = APIRouter(prefix="/agent", tags=["agent planning"])
TaskType = Literal["explain", "bug_fix", "feature", "tests", "refactor", "documentation", "ci_investigation"]


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository: str = Field(min_length=3, max_length=201)
    account: str | None = Field(default=None, max_length=100)
    branch: str = Field(min_length=1, max_length=255)
    task_type: TaskType
    request: str = Field(min_length=8, max_length=5000)
    share_with_ai: bool

    @field_validator("repository")
    @classmethod
    def _valid_repo(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", value):
            raise ValueError("Choose a valid owner/repository.")
        return value

    @field_validator("request")
    @classmethod
    def _nonblank_request(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 8:
            raise ValueError("Describe the task in at least 8 characters.")
        return value


class TaskReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]

def _event(db: Session, task: AgentTask, sequence: int, event_type: str, summary: str) -> None:
    db.add(AgentTaskEvent(task_id=task.id, sequence=sequence, event_type=event_type, summary=summary))


def _serialize(task: AgentTask, db: Session) -> dict[str, Any]:
    events = db.scalars(select(AgentTaskEvent).where(AgentTaskEvent.task_id == task.id).order_by(AgentTaskEvent.sequence)).all()
    return {
        "id": str(task.id), "repository": task.repository, "branch": task.branch,
        "task_type": task.task_type, "request": task.request, "status": task.status,
        "plan": json.loads(task.plan_json) if task.plan_json else None, "model": task.model,
        "failure_reason": task.failure_reason, "created_at": task.created_at.replace(tzinfo=timezone.utc).isoformat(),
        "events": [{"type": e.event_type, "summary": e.summary, "created_at": e.created_at.replace(tzinfo=timezone.utc).isoformat()} for e in events],
        "execution_available": False,
    }


def _task_for_user(task_id: UUID, user: User, workspace: Workspace, db: Session) -> AgentTask:
    task = db.scalar(select(AgentTask).where(
        AgentTask.id == task_id, AgentTask.user_id == user.id, AgentTask.workspace_id == workspace.id,
    ))
    if not task:
        raise HTTPException(status_code=404, detail="Task was not found.")
    return task


@router.get("/status")
def status(context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)]) -> dict[str, Any]:
    settings = get_settings()
    return {"planning_available": bool(settings.openai_api_key), "model": settings.openai_model,
            "execution_available": False, "isolation_ready": False}


@router.post("/tasks")
async def create_task(
    body: TaskCreate,
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    request: Request,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if not body.share_with_ai:
        raise HTTPException(status_code=400, detail="Explicit consent is required to send task context to the AI provider.")
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="AI planning is not configured. Set OPENAI_API_KEY and retry.")
    user, workspace, _ = context
    enforce_auth_rate_limit(request, db, "agent_plan")
    account = selected_account(user, workspace, body.account, db)
    _, _, cipher = require_configuration(settings)
    token = await fresh_access_token(account, settings, cipher, db)
    owner, repo = body.repository.split("/", 1)
    client = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    try:
        repository = await client.get_repository(token, owner, repo)
        branches = await client.list_branches(token, owner, repo)
    except GitHubAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not any(item.get("name") == body.branch for item in branches):
        raise HTTPException(status_code=400, detail="Choose a branch available in the selected repository.")
    readme = None
    try:
        result = await client.get_contents(token, owner, repo, "README.md", body.branch)
        if isinstance(result, dict) and isinstance(result.get("text_content"), str):
            readme = result["text_content"][:8000]
    except GitHubAPIError as exc:
        if "not found" not in str(exc).lower():
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    task = AgentTask(user_id=user.id, workspace_id=workspace.id, github_account_id=account.id,
        github_login=account.login, repository=body.repository, branch=body.branch,
        task_type=body.task_type, request=body.request.strip(), status="planning")
    db.add(task)
    db.flush()
    _event(db, task, 1, "planning_started", "Task context authorized and planning started.")
    db.commit()
    try:
        plan, usage = await generate_plan(settings, {"task_type": task.task_type, "request": task.request, "branch": task.branch}, repository, readme)
    except PlanGenerationError as exc:
        task.status = "failed"
        task.failure_reason = str(exc)[:240]
        _event(db, task, 2, "planning_failed", task.failure_reason)
        db.commit()
        raise HTTPException(status_code=503 if exc.retryable else 502, detail=str(exc)) from exc
    task.plan_json = plan.model_dump_json()
    task.model = settings.openai_model
    task.input_tokens = usage["input_tokens"]
    task.output_tokens = usage["output_tokens"]
    task.status = "awaiting_review"
    _event(db, task, 2, "plan_ready", "Plan is ready for user review. No repository changes were made.")
    db.commit()
    return _serialize(task, db)


@router.get("/tasks")
def list_tasks(context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
               limit: Annotated[int, Query(ge=1, le=100)] = 25, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    user, workspace, _ = context
    tasks = db.scalars(select(AgentTask).where(AgentTask.user_id == user.id, AgentTask.workspace_id == workspace.id)
        .order_by(AgentTask.created_at.desc()).limit(limit)).all()
    return [_serialize(task, db) for task in tasks]


@router.get("/tasks/{task_id}")
def get_task(task_id: UUID, context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
             db: Session = Depends(get_db)) -> dict[str, Any]:
    user, workspace, _ = context
    return _serialize(_task_for_user(task_id, user, workspace, db), db)


@router.post("/tasks/{task_id}/review")
def review_task(
    task_id: UUID,
    body: TaskReview,
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user, workspace, _ = context
    task = _task_for_user(task_id, user, workspace, db)
    if task.status != "awaiting_review":
        raise HTTPException(status_code=409, detail="Only plans awaiting review can receive a review decision.")
    if not task.plan_json:
        raise HTTPException(status_code=409, detail="This task has no completed plan to review.")
    if body.decision == "approve":
        task.status = "review_approved"
        summary = "User approved the plan. No repository changes were made."
        event_type = "review_approved"
    else:
        task.status = "review_rejected"
        summary = "User rejected the plan. No repository changes were made."
        event_type = "review_rejected"
    sequence = (db.query(AgentTaskEvent).filter(AgentTaskEvent.task_id == task.id).count()) + 1
    _event(db, task, sequence, event_type, summary)
    db.commit()
    return _serialize(task, db)
