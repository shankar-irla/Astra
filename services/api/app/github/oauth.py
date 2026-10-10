from __future__ import annotations

import base64
import hashlib
import secrets
import re
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import current_session, enforce_auth_rate_limit, require_csrf, token_digest
from app.core.config import Settings, get_settings
from app.db import get_db
from app.github.client import GitHubAPIError, GitHubClient
from app.models import GitHubAccount, GitHubMutation, GitHubOAuthState, SessionRecord, User, Workspace

router = APIRouter(prefix="/github", tags=["github"])
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"


class IssueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=65536)
    labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Issue title cannot be blank.")
        return value

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, values: list[str]) -> list[str]:
        labels = [value.strip() for value in values]
        if any(not value or len(value) > 50 for value in labels):
            raise ValueError("Labels must contain 1 to 50 characters.")
        if len(set(labels)) != len(labels):
            raise ValueError("Duplicate labels are not allowed.")
        return labels


def require_configuration(settings: Settings) -> tuple[str, str, Fernet]:
    client_id = settings.github_app_client_id
    client_secret = settings.github_app_client_secret.get_secret_value() if settings.github_app_client_secret else ""
    callback_url = settings.github_app_callback_url
    encryption_key = settings.token_encryption_key.get_secret_value() if settings.token_encryption_key else ""
    if not all((client_id, client_secret, callback_url, encryption_key)):
        raise HTTPException(status_code=503, detail="GitHub App authorization is not configured.")
    try:
        cipher = Fernet(encryption_key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="GitHub token encryption is not configured correctly.") from exc
    return client_id, client_secret, cipher


def github_is_configured(settings: Settings) -> bool:
    try:
        require_configuration(settings)
        return True
    except HTTPException:
        return False


def encrypt_token(cipher: Fernet, token: str) -> str:
    return cipher.encrypt(token.encode("utf-8")).decode("ascii")


def decrypt_token(cipher: Fernet, ciphertext: str) -> str:
    try:
        return cipher.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Stored GitHub credentials could not be decrypted.") from exc


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _expiry(payload: dict[str, Any], key: str) -> datetime | None:
    seconds = payload.get(key)
    return datetime.now(timezone.utc) + timedelta(seconds=int(seconds)) if isinstance(seconds, (int, float)) else None


@router.get("/connect")
def connect(context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
            request: Request,
            db: Session = Depends(get_db)) -> RedirectResponse:
    settings = get_settings()
    client_id, _, cipher = require_configuration(settings)
    user, _, session = context
    enforce_auth_rate_limit(request, db, "github")
    db.execute(delete(GitHubOAuthState).where(GitHubOAuthState.expires_at < datetime.now(timezone.utc)))
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    db.add(GitHubOAuthState(
        state_digest=token_digest(state), user_id=user.id, session_id=session.id,
        code_verifier_encrypted=encrypt_token(cipher, verifier),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    ))
    db.commit()
    query = urlencode({"client_id": client_id, "redirect_uri": settings.github_app_callback_url,
                       "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
    return RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{query}", status_code=302)


async def exchange_token(client_id: str, client_secret: str, data: dict[str, str]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(GITHUB_TOKEN_URL, data={"client_id": client_id,
                "client_secret": client_secret, **data}, headers={"Accept": "application/json"})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
            raise HTTPException(status_code=502, detail="GitHub did not return an access token.")
        return result
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="GitHub authorization could not be completed.") from exc


@router.get("/callback")
async def callback(
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    code: Annotated[str | None, Query(max_length=256)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=100)] = None,
    db: Session = Depends(get_db),
) -> Response:
    settings = get_settings()
    if error:
        return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/?github=denied", status_code=303)
    if not code or not state:
        raise HTTPException(status_code=400, detail="GitHub callback is missing required parameters.")
    client_id, client_secret, cipher = require_configuration(settings)
    user, workspace, session = context
    oauth_state = db.scalar(select(GitHubOAuthState).where(
        GitHubOAuthState.state_digest == token_digest(state),
        GitHubOAuthState.user_id == user.id,
        GitHubOAuthState.session_id == session.id,
    ))
    if not oauth_state or utc(oauth_state.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="GitHub authorization state is invalid or expired.")
    verifier = decrypt_token(cipher, oauth_state.code_verifier_encrypted)
    db.delete(oauth_state)
    db.commit()

    token_data = await exchange_token(client_id, client_secret, {
        "code": code, "redirect_uri": settings.github_app_callback_url or "", "code_verifier": verifier,
    })
    access_token = token_data["access_token"]
    github = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    try:
        profile = await github.get_authenticated_user(access_token)
    except GitHubAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    account = db.scalar(select(GitHubAccount).where(
        GitHubAccount.user_id == user.id, GitHubAccount.github_user_id == profile["id"],
    ))
    if account:
        account.workspace_id = workspace.id
        account.login = profile["login"]
        account.avatar_url = profile.get("avatar_url")
        account.access_token_encrypted = encrypt_token(cipher, access_token)
        account.refresh_token_encrypted = encrypt_token(cipher, token_data["refresh_token"]) if token_data.get("refresh_token") else None
        account.access_expires_at = _expiry(token_data, "expires_in")
        account.refresh_expires_at = _expiry(token_data, "refresh_token_expires_in")
        account.revoked_at = None
    else:
        account = GitHubAccount(
            user_id=user.id, workspace_id=workspace.id, github_user_id=profile["id"],
            login=profile["login"], avatar_url=profile.get("avatar_url"),
            access_token_encrypted=encrypt_token(cipher, access_token),
            refresh_token_encrypted=encrypt_token(cipher, token_data["refresh_token"]) if token_data.get("refresh_token") else None,
            access_expires_at=_expiry(token_data, "expires_in"),
            refresh_expires_at=_expiry(token_data, "refresh_token_expires_in"),
        )
        db.add(account)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This GitHub account is already linked.") from exc
    return RedirectResponse(f"{settings.frontend_url.rstrip('/')}/?github=connected", status_code=303)


@router.get("/accounts")
def accounts(context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
             db: Session = Depends(get_db)) -> dict[str, Any]:
    user, workspace, _ = context
    rows = db.scalars(select(GitHubAccount).where(
        GitHubAccount.user_id == user.id, GitHubAccount.workspace_id == workspace.id,
        GitHubAccount.revoked_at.is_(None),
    )).all()
    return {"configured": github_is_configured(get_settings()), "accounts": [
        {"id": str(row.id), "login": row.login, "avatar_url": row.avatar_url,
         "connected_at": utc(row.connected_at).isoformat()} for row in rows
    ]}


@router.get("/activity")
def github_activity(
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    user, workspace, _ = context
    rows = db.scalars(select(GitHubMutation).where(
        GitHubMutation.user_id == user.id,
        GitHubMutation.workspace_id == workspace.id,
    ).order_by(GitHubMutation.created_at.desc()).limit(limit)).all()
    return [{
        "id": str(row.id), "operation": row.operation, "repository": row.repository,
        "github_login": row.github_login, "status": row.status,
        "resource_number": row.external_resource_number, "url": row.external_url,
        "created_at": utc(row.created_at).isoformat(),
    } for row in rows]


async def fresh_access_token(account: GitHubAccount, settings: Settings, cipher: Fernet, db: Session) -> str:
    access = decrypt_token(cipher, account.access_token_encrypted)
    now = datetime.now(timezone.utc)
    if not account.access_expires_at or utc(account.access_expires_at) > now + timedelta(minutes=2):
        return access
    if not account.refresh_token_encrypted or (account.refresh_expires_at and utc(account.refresh_expires_at) <= now):
        raise HTTPException(status_code=401, detail=f"GitHub account {account.login} needs to be reconnected.")
    client_id, client_secret, _ = require_configuration(settings)
    refreshed = await exchange_token(client_id, client_secret, {
        "grant_type": "refresh_token", "refresh_token": decrypt_token(cipher, account.refresh_token_encrypted),
    })
    account.access_token_encrypted = encrypt_token(cipher, refreshed["access_token"])
    if refreshed.get("refresh_token"):
        account.refresh_token_encrypted = encrypt_token(cipher, refreshed["refresh_token"])
    account.access_expires_at = _expiry(refreshed, "expires_in")
    account.refresh_expires_at = _expiry(refreshed, "refresh_token_expires_in")
    db.commit()
    return refreshed["access_token"]


def selected_account(user: User, workspace: Workspace, login: str | None, db: Session) -> GitHubAccount:
    accounts = db.scalars(select(GitHubAccount).where(
        GitHubAccount.user_id == user.id, GitHubAccount.workspace_id == workspace.id,
        GitHubAccount.revoked_at.is_(None),
    ).order_by(GitHubAccount.login)).all()
    if login:
        account = next((item for item in accounts if item.login.casefold() == login.casefold()), None)
    elif len(accounts) == 1:
        account = accounts[0]
    else:
        account = None
    if not account:
        raise HTTPException(status_code=404 if not accounts or login else 400,
                            detail="Choose a connected GitHub account for this repository request.")
    return account


def validate_repo_segment(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) or value in {".", ".."}:
        raise HTTPException(status_code=400, detail="Repository path is invalid.")
    return value


def validate_repo_path(value: str) -> str:
    if len(value) > 1024 or value.startswith("/") or "\\" in value or any(part in {".", ".."} for part in value.split("/")):
        raise HTTPException(status_code=400, detail="Repository path is invalid.")
    return value


@router.get("/repositories/{owner}/{repository}")
async def repository_detail(
    owner: str,
    repository: str,
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    account: Annotated[str | None, Query(max_length=100)] = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    owner, repository = validate_repo_segment(owner), validate_repo_segment(repository)
    user, workspace, _ = context
    settings = get_settings()
    _, _, cipher = require_configuration(settings)
    github_account = selected_account(user, workspace, account, db)
    token = await fresh_access_token(github_account, settings, cipher, db)
    client = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    try:
        result = await client.get_repository(token, owner, repository)
    except GitHubAPIError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    permissions = result.get("permissions") or {}
    if not isinstance(permissions, dict):
        permissions = {}
    license_info = result.get("license") or {}
    return {key: result.get(key) for key in (
        "id", "name", "full_name", "private", "html_url", "description", "homepage",
        "default_branch", "language", "forks_count", "stargazers_count", "watchers_count",
        "open_issues_count", "updated_at", "created_at", "archived", "disabled",
    )} | {
        "owner": (result.get("owner") or {}).get("login"),
        "account": github_account.login,
        "license": license_info.get("spdx_id") if isinstance(license_info, dict) else None,
        "permissions": {key: permissions.get(key) is True for key in ("admin", "maintain", "push", "triage", "pull")},
    }


@router.get("/repositories/{owner}/{repository}/commits")
async def repository_commits(
    owner: str,
    repository: str,
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    account: Annotated[str | None, Query(max_length=100)] = None,
    branch: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    owner, repository = validate_repo_segment(owner), validate_repo_segment(repository)
    user, workspace, _ = context
    settings = get_settings()
    _, _, cipher = require_configuration(settings)
    github_account = selected_account(user, workspace, account, db)
    token = await fresh_access_token(github_account, settings, cipher, db)
    client = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    try:
        commits = await client.list_commits(token, owner, repository, branch, limit)
    except GitHubAPIError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    safe_commits = []
    for item in commits:
        commit = item.get("commit") or {}
        author = commit.get("author") or {}
        safe_commits.append({
            "sha": item.get("sha"),
            "short_sha": item.get("sha", "")[:7],
            "message": (commit.get("message") or "").splitlines()[0][:240],
            "author": author.get("name") or (item.get("author") or {}).get("login") or "Unknown author",
            "date": author.get("date"),
            "html_url": item.get("html_url"),
        })
    return safe_commits


def _safe_issue(item: dict[str, Any]) -> dict[str, Any]:
    user = item.get("user") or {}
    labels = item.get("labels") or []
    safe_labels = []
    for label in labels:
        if not isinstance(label, dict) or not isinstance(label.get("name"), str):
            continue
        color = label.get("color")
        safe_labels.append({"name": label["name"][:100],
                            "color": color if isinstance(color, str) and re.fullmatch(r"[0-9a-fA-F]{6}", color) else None})
    return {
        "number": item.get("number"), "title": item.get("title"), "state": item.get("state"),
        "author": user.get("login"), "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"), "comments": item.get("comments", 0),
        "html_url": item.get("html_url"), "body": (item.get("body") or "")[:2000],
        "labels": safe_labels,
    }


async def _repository_items(owner: str, repository: str, resource: Literal["issues", "pulls"],
                            state: Literal["open", "closed", "all"], limit: int, account: str | None,
                            context: tuple[User, Workspace, SessionRecord], db: Session) -> list[dict[str, Any]]:
    owner, repository = validate_repo_segment(owner), validate_repo_segment(repository)
    user, workspace, _ = context
    settings = get_settings()
    _, _, cipher = require_configuration(settings)
    github_account = selected_account(user, workspace, account, db)
    token = await fresh_access_token(github_account, settings, cipher, db)
    client = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    try:
        records = await client.list_repository_items(token, owner, repository, resource, state, limit)
    except GitHubAPIError as exc:
        status_code = 404 if "not found" in str(exc).lower() else 502
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    # GitHub's issues endpoint also returns pull requests; keep the collections distinct.
    if resource == "issues":
        records = [item for item in records if "pull_request" not in item]
    return [_safe_issue(item) for item in records]


@router.get("/repositories/{owner}/{repository}/issues")
async def repository_issues(
    owner: str,
    repository: str,
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    account: Annotated[str | None, Query(max_length=100)] = None,
    state: Literal["open", "closed", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return await _repository_items(owner, repository, "issues", state, limit, account, context, db)


@router.post("/repositories/{owner}/{repository}/issues", status_code=201, dependencies=[Depends(require_csrf)])
async def create_repository_issue(
    owner: str,
    repository: str,
    payload: IssueCreate,
    response: Response,
    idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    request: Request,
    account: Annotated[str | None, Query(max_length=100)] = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    owner, repository = validate_repo_segment(owner), validate_repo_segment(repository)
    user, workspace, _ = context
    repo_full_name = f"{owner}/{repository}"
    github_account = selected_account(user, workspace, account, db)
    canonical_request = json.dumps({
        "operation": "create_issue", "repository": repo_full_name,
        "title": payload.title, "body": payload.body, "labels": sorted(payload.labels),
    }, sort_keys=True, separators=(",", ":"))
    request_hash = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    key = str(idempotency_key)
    previous = db.scalar(select(GitHubMutation).where(
        GitHubMutation.user_id == user.id, GitHubMutation.workspace_id == workspace.id,
        GitHubMutation.idempotency_key == key,
    ))
    if previous:
        if previous.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="This idempotency key was already used for a different request.")
        if previous.status == "succeeded":
            response.status_code = 200
            return {"number": previous.external_resource_number, "html_url": previous.external_url,
                    "status": previous.status, "replayed": True}
        raise HTTPException(status_code=409, detail=f"This request is {previous.status}; inspect GitHub before retrying.")

    settings = get_settings()
    _, _, cipher = require_configuration(settings)
    token = await fresh_access_token(github_account, settings, cipher, db)
    enforce_auth_rate_limit(request, db, "github")
    mutation = GitHubMutation(
        user_id=user.id, workspace_id=workspace.id, github_account_id=github_account.id,
        github_login=github_account.login,
        idempotency_key=key, request_hash=request_hash, operation="create_issue",
        repository=repo_full_name, status="pending",
    )
    db.add(mutation)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="This request is already being processed. Retry with the same idempotency key.") from exc

    client = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    try:
        created = await client.create_issue(token, owner, repository, payload.title, payload.body, payload.labels)
    except GitHubAPIError as exc:
        mutation.status = "failed"
        db.commit()
        message = str(exc)
        status_code = 403 if "denied" in message.lower() else 404 if "not found" in message.lower() else 422 if "rejected the issue fields" in message.lower() else 502
        raise HTTPException(status_code=status_code, detail=message) from exc
    mutation.status = "succeeded"
    mutation.external_resource_number = created["number"]
    mutation.external_url = created["html_url"]
    db.commit()
    return {"number": created["number"], "title": created.get("title", payload.title),
            "html_url": created["html_url"], "status": "succeeded", "replayed": False}


@router.get("/repositories/{owner}/{repository}/pulls")
async def repository_pull_requests(
    owner: str,
    repository: str,
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    account: Annotated[str | None, Query(max_length=100)] = None,
    state: Literal["open", "closed", "all"] = "open",
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return await _repository_items(owner, repository, "pulls", state, limit, account, context, db)


@router.get("/repositories/{owner}/{repository}/branches")
async def repository_branches(
    owner: str,
    repository: str,
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    account: Annotated[str | None, Query(max_length=100)] = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    owner, repository = validate_repo_segment(owner), validate_repo_segment(repository)
    user, workspace, _ = context
    settings = get_settings()
    _, _, cipher = require_configuration(settings)
    github_account = selected_account(user, workspace, account, db)
    token = await fresh_access_token(github_account, settings, cipher, db)
    client = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    try:
        branches = await client.list_branches(token, owner, repository)
    except GitHubAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [{"name": item.get("name"), "protected": item.get("protected", False),
             "commit_sha": (item.get("commit") or {}).get("sha")} for item in branches]


@router.get("/repositories/{owner}/{repository}/contents")
async def repository_contents(
    owner: str,
    repository: str,
    context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
    account: Annotated[str | None, Query(max_length=100)] = None,
    path: Annotated[str, Query(max_length=1024)] = "",
    ref: Annotated[str | None, Query(max_length=255)] = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    owner, repository, path = validate_repo_segment(owner), validate_repo_segment(repository), validate_repo_path(path)
    user, workspace, _ = context
    settings = get_settings()
    _, _, cipher = require_configuration(settings)
    github_account = selected_account(user, workspace, account, db)
    token = await fresh_access_token(github_account, settings, cipher, db)
    client = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    try:
        result = await client.get_contents(token, owner, repository, path, ref)
    except GitHubAPIError as exc:
        code = 413 if "1 MB" in str(exc) else 502
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    if isinstance(result, list):
        return {"type": "directory", "path": path, "entries": [
            {key: item.get(key) for key in ("name", "path", "type", "size", "html_url")}
            for item in result
        ]}
    return {"type": result.get("type"), "name": result.get("name"), "path": result.get("path"),
            "size": result.get("size"), "html_url": result.get("html_url"),
            "encoding": "utf-8" if isinstance(result.get("text_content"), str) else "binary",
            "content": result.get("text_content")}


@router.get("/repositories")
async def repositories(context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
                       db: Session = Depends(get_db)) -> dict[str, Any]:
    user, workspace, _ = context
    settings = get_settings()
    accounts = db.scalars(select(GitHubAccount).where(
        GitHubAccount.user_id == user.id, GitHubAccount.workspace_id == workspace.id,
        GitHubAccount.revoked_at.is_(None),
    )).all()
    if not accounts:
        return {"repositories": [], "warnings": []}
    _, _, cipher = require_configuration(settings)
    client = GitHubClient(settings.github_api_url, settings.github_api_version, settings.github_request_timeout_seconds)
    repos: dict[str, dict[str, Any]] = {}
    warnings = []
    for account in accounts:
        try:
            token = await fresh_access_token(account, settings, cipher, db)
            for repo in await client.list_repositories(token):
                owner = repo.get("owner") or {}
                full_name = repo.get("full_name")
                if full_name:
                    repos[full_name] = {key: repo.get(key) for key in (
                        "id", "name", "full_name", "private", "html_url", "description", "default_branch", "updated_at"
                    )} | {"owner": owner.get("login"), "account": account.login}
        except (HTTPException, GitHubAPIError) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            warnings.append({"account": account.login, "message": detail})
    return {"repositories": sorted(repos.values(), key=lambda repo: repo.get("updated_at") or "", reverse=True),
            "warnings": warnings}


@router.delete("/accounts/{account_id}", dependencies=[Depends(require_csrf)])
async def disconnect(account_id: UUID,
                     context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
                     db: Session = Depends(get_db)) -> dict[str, str]:
    user, workspace, _ = context
    settings = get_settings()
    client_id, client_secret, cipher = require_configuration(settings)
    account = db.scalar(select(GitHubAccount).where(
        GitHubAccount.id == account_id, GitHubAccount.user_id == user.id,
        GitHubAccount.workspace_id == workspace.id, GitHubAccount.revoked_at.is_(None),
    ))
    if not account:
        raise HTTPException(status_code=404, detail="GitHub account not found.")
    access = decrypt_token(cipher, account.access_token_encrypted)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.delete(
                f"{settings.github_api_url.rstrip('/')}/applications/{client_id}/grant",
                auth=(client_id, client_secret), json={"access_token": access},
                headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": settings.github_api_version},
            )
        if response.status_code not in {204, 404, 422}:
            raise HTTPException(status_code=502, detail="GitHub could not revoke the authorization.")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="GitHub could not revoke the authorization.") from exc
    db.delete(account)
    db.commit()
    return {"status": "disconnected"}
