from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db
from app.models import AuthAttempt, SessionRecord, User, Workspace, WorkspaceMembership

router = APIRouter(prefix="/auth", tags=["authentication"])
password_hasher = PasswordHasher()
dummy_password_hash = password_hasher.hash("astra-invalid-login-timing-padding")
CSRF_COOKIE = "astra_csrf"
SESSION_COOKIE = "astra_session"


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized.count("@") != 1 or "." not in normalized.rsplit("@", 1)[-1] or any(c.isspace() for c in normalized):
            raise ValueError("Enter a valid email address.")
        return normalized


def enforce_auth_rate_limit(request: Request, db: Session, action: str) -> None:
    now = datetime.now(timezone.utc)
    ip = request.client.host if request.client else "unknown"
    fingerprint = hashlib.sha256(ip.encode("utf-8")).hexdigest()
    window = timedelta(hours=1) if action == "register" else timedelta(minutes=15)
    maximum = 10 if action == "register" else 20
    cutoff = now - window
    count = db.scalar(select(func.count()).select_from(AuthAttempt).where(
        AuthAttempt.ip_fingerprint == fingerprint,
        AuthAttempt.action == action,
        AuthAttempt.occurred_at >= cutoff,
    )) or 0
    if count >= maximum:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
    db.execute(delete(AuthAttempt).where(AuthAttempt.occurred_at < now - timedelta(days=1)))
    db.add(AuthAttempt(ip_fingerprint=fingerprint, action=action))
    db.commit()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_csrf(
    request: Request,
    header_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
) -> None:
    settings = get_settings()
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in {value.rstrip("/") for value in settings.allowed_origins}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Request origin is not allowed.")
    if not csrf_cookie or not header_token or not compare_digest(csrf_cookie, header_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed.")


def start_session(db: Session, user: User, workspace: Workspace, response: Response) -> None:
    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)
    db.add(SessionRecord(
        user_id=user.id,
        workspace_id=workspace.id,
        token_digest=token_digest(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.session_ttl_seconds),
    ))
    db.commit()
    response.set_cookie(SESSION_COOKIE, raw_token, max_age=settings.session_ttl_seconds, httponly=True,
                        secure=settings.session_cookie_secure, samesite="lax", path="/")


def current_session(
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    db: Session = Depends(get_db),
) -> tuple[User, Workspace, SessionRecord]:
    if not session_token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    record = db.scalar(select(SessionRecord).where(SessionRecord.token_digest == token_digest(session_token)))
    now = datetime.now(timezone.utc)
    expiration = record.expires_at if record and record.expires_at.tzinfo else (
        record.expires_at.replace(tzinfo=timezone.utc) if record else now
    )
    if not record or record.revoked_at or expiration <= now:
        raise HTTPException(status_code=401, detail="Authentication required.")
    membership = db.scalar(select(WorkspaceMembership).where(
        WorkspaceMembership.user_id == record.user_id,
        WorkspaceMembership.workspace_id == record.workspace_id,
    ))
    user = db.get(User, record.user_id)
    workspace = db.get(Workspace, record.workspace_id)
    if not membership or not user or not workspace:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user, workspace, record


@router.get("/csrf")
def csrf_token(response: Response) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    settings = get_settings()
    response.set_cookie(CSRF_COOKIE, token, max_age=3600, httponly=False,
                        secure=settings.session_cookie_secure, samesite="lax", path="/")
    return {"csrf_token": token}


@router.post("/register", status_code=201, dependencies=[Depends(require_csrf)])
def register(credentials: Credentials, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    enforce_auth_rate_limit(request, db, "register")
    user = User(email=credentials.email, password_hash=password_hasher.hash(credentials.password))
    db.add(user)
    try:
        db.flush()
        workspace = Workspace(name="Personal workspace", slug=f"personal-{user.id.hex[:16]}")
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="owner"))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Unable to create account with these details.") from exc
    start_session(db, user, workspace, response)
    return {"id": str(user.id), "email": user.email, "workspace_id": str(workspace.id)}


@router.post("/login", dependencies=[Depends(require_csrf)])
def login(credentials: Credentials, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    enforce_auth_rate_limit(request, db, "login")
    user = db.scalar(select(User).where(User.email == credentials.email))
    try:
        password_hasher.verify(user.password_hash if user else dummy_password_hash, credentials.password)
        verified = user is not None
    except VerificationError:
        verified = False
    if not verified or not user:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    membership = db.scalar(select(WorkspaceMembership).where(
        WorkspaceMembership.user_id == user.id,
    ).order_by(WorkspaceMembership.created_at))
    workspace = db.get(Workspace, membership.workspace_id) if membership else None
    if not workspace:
        raise HTTPException(status_code=403, detail="No workspace is available for this account.")
    start_session(db, user, workspace, response)
    return {"id": str(user.id), "email": user.email, "workspace_id": str(workspace.id)}


@router.get("/me")
def me(context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)]) -> dict[str, str]:
    user, workspace, _ = context
    return {"id": str(user.id), "email": user.email, "workspace_id": str(workspace.id), "workspace_name": workspace.name}


@router.get("/workspaces")
def list_workspaces(context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
                    db: Session = Depends(get_db)) -> list[dict[str, str]]:
    user, _, _ = context
    memberships = db.scalars(select(WorkspaceMembership).where(WorkspaceMembership.user_id == user.id)).all()
    result = []
    for membership in memberships:
        workspace = db.get(Workspace, membership.workspace_id)
        if workspace:
            result.append({"id": str(workspace.id), "role": membership.role, "name": workspace.name})
    return result


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(response: Response,
           context: Annotated[tuple[User, Workspace, SessionRecord], Depends(current_session)],
           db: Session = Depends(get_db)) -> dict[str, str]:
    _, _, record = context
    record.revoked_at = datetime.now(timezone.utc)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                           secure=get_settings().session_cookie_secure, samesite="lax")
    return {"status": "signed_out"}
