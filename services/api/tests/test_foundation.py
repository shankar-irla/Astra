import httpx
import pytest
import hashlib
import json
from types import SimpleNamespace
from datetime import datetime, timezone
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Column, DateTime, MetaData, String, Table, Uuid, select
from sqlalchemy.orm import sessionmaker
from uuid import UUID, uuid4

from app import main
from app.db import Base, build_engine, check_database, get_db
from app.github.client import GitHubAPIError, GitHubClient
from app import models  # noqa: F401
from app.core.config import get_settings
from app.auth import enforce_auth_rate_limit
from app.models import AuthAttempt
from app.github.oauth import validate_repo_path
from fastapi import HTTPException
from starlette.requests import Request
from pydantic import SecretStr
from cryptography.fernet import Fernet
from urllib.parse import parse_qs, urlparse
import app.github.oauth as github_oauth
from app.core.config import Settings
from app.agent.planner import PlanGenerationError, PlanStep, TaskPlan, generate_plan, validate_plan
import app.agent.router as agent_router


def test_health_and_readiness(monkeypatch):
    monkeypatch.setattr(main, "check_database", lambda: None)
    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").json() == {"status": "ready", "database": "ok"}


def test_readiness_reports_database_failure(monkeypatch):
    def unavailable():
        raise OSError("private connection detail")

    monkeypatch.setattr(main, "check_database", unavailable)
    with TestClient(main.app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}


def test_cors_allows_csrf_protected_github_disconnect_preflight():
    with TestClient(main.app) as client:
        response = client.options("/github/accounts/00000000-0000-0000-0000-000000000001", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "x-csrf-token",
        })
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_database_connection_check_works_with_sqlite():
    check_database(build_engine("sqlite:///:memory:"))


@pytest.mark.asyncio
async def test_github_repository_listing_uses_auth_and_paginates(monkeypatch):
    calls = []

    async def fake_get(self, url, headers, params=None):
        calls.append((url, headers, params))
        if len(calls) == 1:
            request = httpx.Request("GET", url)
            return httpx.Response(200, json=[{"id": 1}], headers={"Link": '<https://api.example.test/page2>; rel="next"'}, request=request)
        return httpx.Response(200, json=[{"id": 2}], request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await GitHubClient("https://api.example.test", "2022-11-28").list_repositories("secret-token")
    assert [repo["id"] for repo in result] == [1, 2]
    assert len(calls) == 2
    assert calls[0][1]["Authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_github_rejects_missing_credential():
    with pytest.raises(GitHubAPIError, match="credential is required"):
        await GitHubClient("https://api.example.test", "2022-11-28").list_repositories("")


@pytest.mark.asyncio
async def test_github_contents_reads_utf8_file_and_uses_selected_ref(monkeypatch):
    calls = []

    async def fake_get(self, url, headers, params=None):
        calls.append((url, headers, params))
        return httpx.Response(200, json={"type": "file", "encoding": "base64", "content": "SGVsbG8gQXN0cmEh", "size": 12},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    result = await GitHubClient("https://api.example.test", "2022-11-28").get_contents(
        "token", "org", "repo", "src/main.py", "feature/example")
    assert result["text_content"] == "Hello Astra!"
    assert calls[0][0].endswith("/repos/org/repo/contents/src/main.py")
    assert calls[0][2] == {"ref": "feature/example"}
    assert calls[0][1]["Authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_github_repository_detail_and_commit_history_are_bounded(monkeypatch):
    calls = []

    async def fake_get(self, url, headers, params=None):
        calls.append((url, headers, params))
        if url.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": "abc123", "commit": {"message": "change", "author": {"name": "Dev"}}}],
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, json={"id": 1, "full_name": "org/repo", "private": True},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    client = GitHubClient("https://api.example.test", "2022-11-28")
    detail = await client.get_repository("token", "org", "repo")
    commits = await client.list_commits("token", "org", "repo", "main", limit=10)
    assert detail["full_name"] == "org/repo"
    assert commits[0]["sha"] == "abc123"
    assert calls[0][0].endswith("/repos/org/repo")
    assert calls[1][2] == {"per_page": 10, "sha": "main"}
    assert calls[1][1]["Authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_github_issue_creation_sends_validated_payload(monkeypatch):
    calls = []

    async def fake_post(self, url, headers, json):
        calls.append((url, headers, json))
        return httpx.Response(201, json={"number": 23, "title": json["title"],
                                        "html_url": "https://github.com/acme/app/issues/23"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    result = await GitHubClient("https://api.example.test", "2022-11-28").create_issue(
        "token", "acme", "app", "Report a defect", "Observed behavior", ["bug"])
    assert result["number"] == 23
    assert calls[0][0].endswith("/repos/acme/app/issues")
    assert calls[0][2] == {"title": "Report a defect", "body": "Observed behavior", "labels": ["bug"]}
    assert calls[0][1]["Authorization"] == "Bearer token"


def test_repository_path_blocks_traversal_and_absolute_paths():
    with pytest.raises(HTTPException, match="path is invalid"):
        validate_repo_path("src/../../secrets")
    with pytest.raises(HTTPException, match="path is invalid"):
        validate_repo_path("/etc/passwd")
    assert validate_repo_path("src/api/main.py") == "src/api/main.py"


@pytest.fixture
def auth_client(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_db():
        with factory() as session:
            yield session

    main.app.dependency_overrides[get_db] = override_db
    with TestClient(main.app) as client:
        yield client
    main.app.dependency_overrides.clear()
    engine.dispose()


def register_user(client, email: str):
    csrf = client.get("/auth/csrf").json()["csrf_token"]
    return client.post("/auth/register", headers={"X-CSRF-Token": csrf}, json={
        "email": email,
        "password": "correct-horse-battery-staple",
    })


def test_registration_creates_workspace_scoped_session_and_logout_revokes(auth_client):
    response = register_user(auth_client, "Dev@Example.com")
    assert response.status_code == 201
    assert response.json()["email"] == "dev@example.com"
    cookie = response.cookies.get("astra_session")
    assert cookie and len(cookie) >= 40
    assert response.headers.get("set-cookie", "").lower().find("httponly") >= 0

    identity = auth_client.get("/auth/me")
    assert identity.status_code == 200
    assert identity.json()["workspace_name"] == "Personal workspace"
    workspaces = auth_client.get("/auth/workspaces").json()
    assert len(workspaces) == 1
    assert workspaces[0]["id"] == identity.json()["workspace_id"]
    assert workspaces[0]["role"] == "owner"

    csrf = auth_client.get("/auth/csrf").json()["csrf_token"]
    assert auth_client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert auth_client.get("/auth/me").status_code == 401


def test_auth_requires_csrf_and_rejects_duplicate_registration(auth_client):
    assert auth_client.post("/auth/register", json={
        "email": "person@example.com", "password": "correct-horse-battery-staple"
    }).status_code == 403
    assert register_user(auth_client, "person@example.com").status_code == 201
    duplicate = register_user(auth_client, "PERSON@example.com")
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "Unable to create account with these details."


def test_agent_planner_status_requires_identity_and_reports_execution_disabled(auth_client, monkeypatch):
    assert auth_client.get("/agent/status").status_code == 401
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()
    assert register_user(auth_client, "planner-status@example.com").status_code == 201
    status = auth_client.get("/agent/status")
    assert status.status_code == 200
    assert status.json()["planning_available"] is True
    assert status.json()["execution_available"] is False
    get_settings.cache_clear()


def test_agent_planner_requires_explicit_ai_consent(auth_client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()
    assert register_user(auth_client, "planner-consent@example.com").status_code == 201
    csrf = auth_client.get("/auth/csrf").json()["csrf_token"]
    response = auth_client.post("/agent/tasks", headers={"X-CSRF-Token": csrf}, json={
        "repository": "owner/repo", "branch": "main", "task_type": "feature",
        "request": "Add a feature safely", "share_with_ai": False,
    })
    assert response.status_code == 400
    assert "consent" in response.json()["detail"].lower()
    get_settings.cache_clear()


def test_planner_rejects_cycles_and_accepts_dependency_ordering():
    valid = TaskPlan(summary="Add feature", risks=[], steps=[
        PlanStep(id="one", title="Inspect", description="Review the code.", dependencies=[], verification="Inspect changes."),
        PlanStep(id="two", title="Implement", description="Make the change.", dependencies=["one"], verification="Run checks."),
    ], acceptance_criteria=["Feature works"], verification_plan=["Run relevant checks"])
    assert validate_plan(valid) == valid
    cyclic = valid.model_copy(update={"steps": [
        valid.steps[0].model_copy(update={"dependencies": ["two"]}),
        valid.steps[1].model_copy(update={"dependencies": ["one"]}),
    ]})
    with pytest.raises(PlanGenerationError, match="cyclic"):
        validate_plan(cyclic)


@pytest.mark.asyncio
async def test_openai_planner_uses_nonstored_structured_output_and_bounds_context(monkeypatch):
    plan_payload = {
        "summary": "Improve documentation",
        "risks": [],
        "steps": [{"id": "read", "title": "Review docs", "description": "Inspect existing docs.",
                   "dependencies": [], "verification": "Check rendering."}],
        "acceptance_criteria": ["Docs explain the feature"],
        "verification_plan": ["Review changed pages"],
    }
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"status": "completed", "output": [{"type": "message", "content": [{
                "type": "output_text", "text": json.dumps(plan_payload),
            }]}], "usage": "unexpected-shape"}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            captured.update({"url": url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setattr("app.agent.planner.httpx.AsyncClient", FakeClient)
    settings = Settings(openai_api_key=SecretStr("test-key"))
    task = {"task_type": "documentation", "request": "Improve docs", "branch": "main"}
    repository = {"full_name": "owner/repo", "description": "sample", "private": True}
    generated, usage = await generate_plan(settings, task, repository, "R" * 9000)
    assert generated.summary == "Improve documentation"
    assert usage == {"input_tokens": 0, "output_tokens": 0}
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["body"]["store"] is False
    assert captured["body"]["text"]["format"]["strict"] is True
    sent_context = json.loads(captured["body"]["input"][1]["content"])
    assert len(sent_context["readme_excerpt_untrusted"]) == 8000


def test_agent_task_plan_persists_review_and_is_user_scoped(auth_client, monkeypatch):
    settings = Settings(openai_api_key=SecretStr("test-key"))
    monkeypatch.setattr(agent_router, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_router, "selected_account", lambda *args: SimpleNamespace(id=None, login="planner-test"))
    monkeypatch.setattr(agent_router, "require_configuration", lambda _: ("client", "secret", object()))

    async def token(*args):
        return "github-token"

    async def repository(self, access_token, owner, repo):
        assert access_token == "github-token"
        assert (owner, repo) == ("owner", "repo")
        return {"full_name": "owner/repo", "description": "test repository", "default_branch": "main"}

    async def branches(self, access_token, owner, repo):
        return [{"name": "main"}]

    async def contents(self, access_token, owner, repo, path, ref):
        assert (path, ref) == ("README.md", "main")
        return {"text_content": "Repository context"}

    plan = TaskPlan(summary="Document the feature", risks=[], steps=[
        PlanStep(id="inspect", title="Inspect", description="Review existing docs.", dependencies=[], verification="Check links."),
    ], acceptance_criteria=["Documentation is clear"], verification_plan=["Review the rendered README"])
    planner_inputs = []

    async def generate(settings, task, repo, readme):
        planner_inputs.append((task, repo, readme))
        return plan, {"input_tokens": 40, "output_tokens": 30}

    monkeypatch.setattr(agent_router, "fresh_access_token", token)
    monkeypatch.setattr(agent_router.GitHubClient, "get_repository", repository)
    monkeypatch.setattr(agent_router.GitHubClient, "list_branches", branches)
    monkeypatch.setattr(agent_router.GitHubClient, "get_contents", contents)
    monkeypatch.setattr(agent_router, "generate_plan", generate)

    assert register_user(auth_client, "planner-plan@example.com").status_code == 201
    csrf = auth_client.get("/auth/csrf").json()["csrf_token"]
    created = auth_client.post("/agent/tasks", headers={"X-CSRF-Token": csrf}, json={
        "repository": "owner/repo", "account": "planner-test", "branch": "main", "task_type": "documentation",
        "request": "Improve the project documentation", "share_with_ai": True,
    })
    assert created.status_code == 200, created.text
    task = created.json()
    assert task["status"] == "awaiting_review" and task["execution_available"] is False
    assert task["plan"]["summary"] == plan.summary
    assert [event["type"] for event in task["events"]] == ["planning_started", "plan_ready"]
    assert planner_inputs[0][2] == "Repository context"
    assert auth_client.get("/agent/tasks").json()[0]["id"] == task["id"]
    assert auth_client.get(f"/agent/tasks/{task['id']}").status_code == 200

    csrf = auth_client.get("/auth/csrf").json()["csrf_token"]
    reviewed = auth_client.post(f"/agent/tasks/{task['id']}/review", headers={"X-CSRF-Token": csrf}, json={"decision": "approve"})
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "review_approved"
    assert reviewed.json()["events"][-1]["type"] == "review_approved"
    repeated_review = auth_client.post(f"/agent/tasks/{task['id']}/review", headers={"X-CSRF-Token": csrf}, json={"decision": "reject"})
    assert repeated_review.status_code == 409

    with TestClient(main.app) as other_user:
        assert register_user(other_user, "another-planner@example.com").status_code == 201
        assert other_user.get(f"/agent/tasks/{task['id']}").status_code == 404
        assert other_user.get("/agent/tasks").json() == []


def test_workspace_list_is_scoped_to_authenticated_user(auth_client):
    first = register_user(auth_client, "first@example.com").json()
    with TestClient(main.app) as second_client:
        second = register_user(second_client, "second@example.com").json()
        assert first["workspace_id"] != second["workspace_id"]
        assert [workspace["id"] for workspace in second_client.get("/auth/workspaces").json()] == [second["workspace_id"]]
    assert [workspace["id"] for workspace in auth_client.get("/auth/workspaces").json()] == [first["workspace_id"]]


def test_workspace_migration_backfills_existing_users_and_sessions(tmp_path, monkeypatch, request):
    database_url = f"sqlite:///{tmp_path / 'upgrade.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)
    config = Config("services/api/alembic.ini")
    command.upgrade(config, "0001_identity")
    engine = build_engine(database_url)
    user_id, session_id = uuid4(), uuid4()
    metadata = MetaData()
    users = Table("users", metadata, Column("id", Uuid()), Column("email", String()),
                  Column("password_hash", String()), Column("created_at", DateTime(timezone=True)))
    sessions = Table("sessions", metadata, Column("id", Uuid()), Column("user_id", Uuid()),
                     Column("token_digest", String()), Column("created_at", DateTime(timezone=True)),
                     Column("expires_at", DateTime(timezone=True)), Column("revoked_at", DateTime(timezone=True)))
    with engine.begin() as connection:
        connection.execute(users.insert().values(id=user_id, email="legacy@example.com", password_hash="hash"))
        connection.execute(sessions.insert().values(id=session_id, user_id=user_id, token_digest="a" * 64,
                                                     expires_at=datetime(2035, 1, 1, tzinfo=timezone.utc)))
    command.upgrade(config, "head")
    migrated_metadata = MetaData()
    migrated_sessions = Table("sessions", migrated_metadata, autoload_with=engine)
    memberships = Table("workspace_memberships", migrated_metadata, autoload_with=engine)
    with engine.connect() as connection:
        result = connection.execute(select(migrated_sessions.c.workspace_id).where(migrated_sessions.c.id == session_id)).scalar_one()
        assert result is not None
        stored_user_id = connection.execute(select(memberships.c.user_id)).scalar_one()
        assert UUID(stored_user_id) == user_id
    engine.dispose()


def test_mutation_audit_migration_preserves_records_and_github_login(tmp_path, monkeypatch, request):
    database_url = f"sqlite:///{tmp_path / 'mutations.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)
    config = Config("services/api/alembic.ini")
    command.upgrade(config, "0005_github_mutations")
    engine = build_engine(database_url)
    user_id, workspace_id, account_id, mutation_id = uuid4(), uuid4(), uuid4(), uuid4()
    users = Table("users", MetaData(), autoload_with=engine)
    workspaces = Table("workspaces", MetaData(), autoload_with=engine)
    accounts = Table("github_accounts", MetaData(), autoload_with=engine)
    mutations = Table("github_mutations", MetaData(), autoload_with=engine)
    with engine.begin() as connection:
        connection.execute(users.insert().values(id=user_id.hex, email="audit@example.com", password_hash="hash"))
        connection.execute(workspaces.insert().values(id=workspace_id.hex, name="Personal", slug="audit-personal"))
        connection.execute(accounts.insert().values(
            id=account_id.hex, user_id=user_id.hex, workspace_id=workspace_id.hex, github_user_id=9191,
            login="audit-github-user", access_token_encrypted="ciphertext",
        ))
        connection.execute(mutations.insert().values(
            id=mutation_id.hex, user_id=user_id.hex, workspace_id=workspace_id.hex, github_account_id=account_id.hex,
            idempotency_key=str(uuid4()), request_hash="f" * 64, operation="create_issue",
            repository="org/repo", status="succeeded", external_resource_number=7,
            external_url="https://github.com/org/repo/issues/7",
        ))
    command.upgrade(config, "head")
    upgraded_mutations = Table("github_mutations", MetaData(), autoload_with=engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.commit()
        connection.execute(accounts.delete().where(accounts.c.id == account_id.hex))
        connection.commit()
    with engine.connect() as connection:
        record = connection.execute(select(upgraded_mutations.c.github_login,
                                           upgraded_mutations.c.github_account_id,
                                           upgraded_mutations.c.external_resource_number)
                                    .where(upgraded_mutations.c.id == mutation_id.hex)).one()
    assert record.github_login == "audit-github-user"
    assert record.github_account_id is None
    assert record.external_resource_number == 7
    engine.dispose()


def test_auth_rate_limit_returns_retryable_429():
    engine = build_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    request = Request({"type": "http", "method": "POST", "path": "/auth/login", "query_string": b"",
                       "headers": [], "server": ("testserver", 80), "client": ("192.0.2.10", 1234)})
    with factory() as session:
        for _ in range(20):
            session.add(AuthAttempt(ip_fingerprint=hashlib.sha256(b"192.0.2.10").hexdigest(), action="login"))
        session.commit()
        with pytest.raises(HTTPException) as error:
            enforce_auth_rate_limit(request, session, "login")
    assert error.value.status_code == 429
    engine.dispose()


def test_github_oauth_connect_callback_and_repository_scope(auth_client, monkeypatch):
    key = Fernet.generate_key().decode("ascii")
    settings = Settings(
        github_app_client_id="test-client-id",
        github_app_client_secret=SecretStr("test-client-secret"),
        github_app_callback_url="http://127.0.0.1:8000/github/callback",
        token_encryption_key=SecretStr(key),
        frontend_url="http://127.0.0.1:3000",
    )
    monkeypatch.setattr(github_oauth, "get_settings", lambda: settings)
    issue_calls = []

    async def exchange(*_args, **_kwargs):
        return {"access_token": "real-looking-token", "refresh_token": "refresh-token",
                "expires_in": 28800, "refresh_token_expires_in": 15897600}

    async def github_user(self, token):
        assert token == "real-looking-token"
        return {"id": 78321, "login": "astra-test-user", "avatar_url": "https://avatars.example.test/78321"}

    async def repository_list(self, token):
        assert token == "real-looking-token"
        return [{"id": 9, "name": "sample", "full_name": "astra-test-user/sample", "private": True,
                 "html_url": "https://github.com/astra-test-user/sample", "default_branch": "main",
                 "updated_at": "2026-10-01T00:00:00Z", "owner": {"login": "astra-test-user"}}]

    async def branch_list(self, token, owner, repository):
        assert token == "real-looking-token"
        assert (owner, repository) == ("astra-test-user", "sample")
        return [{"name": "main", "protected": True, "commit": {"sha": "abc123"}}]

    async def file_contents(self, token, owner, repository, path, ref):
        assert token == "real-looking-token"
        assert (owner, repository, path, ref) == ("astra-test-user", "sample", "README.md", "main")
        return {"type": "file", "name": "README.md", "path": path, "size": 12,
                "html_url": "https://github.com/astra-test-user/sample/blob/main/README.md", "text_content": "Hello Astra!"}

    async def repository_detail(self, token, owner, repository):
        assert token == "real-looking-token"
        assert (owner, repository) == ("astra-test-user", "sample")
        return {"id": 9, "name": "sample", "full_name": "astra-test-user/sample", "private": True,
                "default_branch": "main", "language": "Python", "stargazers_count": 2,
                "owner": {"login": "astra-test-user"}, "permissions": {"pull": True, "push": False},
                "license": {"spdx_id": "MIT"}}

    async def commit_history(self, token, owner, repository, branch, limit):
        assert token == "real-looking-token"
        assert (owner, repository, branch, limit) == ("astra-test-user", "sample", "main", 20)
        return [{"sha": "abcdef012345", "html_url": "https://github.com/astra-test-user/sample/commit/abcdef",
                 "commit": {"message": "Add feature\n\nDetails", "author": {"name": "Astra Dev", "date": "2026-10-01T00:00:00Z"}}}]

    async def repository_items(self, token, owner, repository, resource, state, limit):
        assert token == "real-looking-token"
        assert (owner, repository, state, limit) == ("astra-test-user", "sample", "open", 30)
        assert resource in {"issues", "pulls"}
        records = [{"number": 3, "title": "A real issue", "state": "open", "user": {"login": "reporter"},
                    "labels": [{"name": "bug", "color": "cc0000"}], "body": "Repro steps", "comments": 2,
                    "html_url": "https://github.com/astra-test-user/sample/issues/3"}]
        if resource == "issues":
            records.append({"number": 4, "title": "This is a PR", "state": "open", "pull_request": {}})
        return records

    async def create_issue(self, token, owner, repository, title, body, labels):
        issue_calls.append((token, owner, repository, title, body, labels))
        return {"number": 27, "title": title, "html_url": "https://github.com/astra-test-user/sample/issues/27"}

    monkeypatch.setattr(github_oauth, "exchange_token", exchange)
    monkeypatch.setattr(github_oauth.GitHubClient, "get_authenticated_user", github_user)
    monkeypatch.setattr(github_oauth.GitHubClient, "list_repositories", repository_list)
    monkeypatch.setattr(github_oauth.GitHubClient, "list_branches", branch_list)
    monkeypatch.setattr(github_oauth.GitHubClient, "get_contents", file_contents)
    monkeypatch.setattr(github_oauth.GitHubClient, "get_repository", repository_detail)
    monkeypatch.setattr(github_oauth.GitHubClient, "list_commits", commit_history)
    monkeypatch.setattr(github_oauth.GitHubClient, "list_repository_items", repository_items)
    monkeypatch.setattr(github_oauth.GitHubClient, "create_issue", create_issue)

    assert register_user(auth_client, "github-owner@example.com").status_code == 201
    connected = auth_client.get("/github/connect", follow_redirects=False)
    assert connected.status_code == 302
    query = parse_qs(urlparse(connected.headers["location"]).query)
    state = query["state"][0]
    assert query["code_challenge_method"] == ["S256"]
    callback = auth_client.get("/github/callback", params={"code": "one-time-code", "state": state}, follow_redirects=False)
    assert callback.status_code == 303
    assert "github=connected" in callback.headers["location"]

    accounts = auth_client.get("/github/accounts").json()["accounts"]
    assert len(accounts) == 1 and accounts[0]["login"] == "astra-test-user"
    repositories = auth_client.get("/github/repositories").json()["repositories"]
    assert [repo["full_name"] for repo in repositories] == ["astra-test-user/sample"]
    detail = auth_client.get("/github/repositories/astra-test-user/sample").json()
    assert detail["language"] == "Python" and detail["permissions"]["push"] is False
    commits = auth_client.get("/github/repositories/astra-test-user/sample/commits", params={"branch": "main"}).json()
    assert commits[0]["message"] == "Add feature" and commits[0]["short_sha"] == "abcdef0"
    issues = auth_client.get("/github/repositories/astra-test-user/sample/issues").json()
    assert len(issues) == 1 and issues[0]["title"] == "A real issue" and issues[0]["labels"][0]["name"] == "bug"
    pull_requests = auth_client.get("/github/repositories/astra-test-user/sample/pulls").json()
    assert len(pull_requests) == 1 and pull_requests[0]["number"] == 3
    csrf = auth_client.get("/auth/csrf").json()["csrf_token"]
    idempotency_key = str(uuid4())
    create_headers = {"X-CSRF-Token": csrf, "Idempotency-Key": idempotency_key}
    issue_payload = {"title": "New API issue", "body": "Created from Astra", "labels": ["bug"]}
    created_issue = auth_client.post("/github/repositories/astra-test-user/sample/issues",
                                     headers=create_headers, json=issue_payload)
    assert created_issue.status_code == 201 and created_issue.json()["number"] == 27
    activity = auth_client.get("/github/activity").json()
    assert activity[0]["operation"] == "create_issue" and activity[0]["resource_number"] == 27
    replayed_issue = auth_client.post("/github/repositories/astra-test-user/sample/issues",
                                      headers=create_headers, json=issue_payload)
    assert replayed_issue.status_code == 200 and replayed_issue.json()["replayed"] is True
    assert len(issue_calls) == 1
    changed_payload = issue_payload | {"title": "A different issue"}
    assert auth_client.post("/github/repositories/astra-test-user/sample/issues",
                            headers=create_headers, json=changed_payload).status_code == 409
    assert auth_client.post("/github/repositories/astra-test-user/sample/issues",
                            json=issue_payload).status_code == 403
    branches = auth_client.get("/github/repositories/astra-test-user/sample/branches").json()
    assert branches == [{"name": "main", "protected": True, "commit_sha": "abc123"}]
    contents = auth_client.get("/github/repositories/astra-test-user/sample/contents",
                               params={"path": "README.md", "ref": "main"}).json()
    assert contents["content"] == "Hello Astra!" and contents["encoding"] == "utf-8"
    replay = auth_client.get("/github/callback", params={"code": "replay", "state": state}, follow_redirects=False)
    assert replay.status_code == 400

    with TestClient(main.app) as another_user:
        assert register_user(another_user, "different-owner@example.com").status_code == 201
        assert another_user.get("/github/repositories/astra-test-user/sample/branches").status_code == 404
        assert another_user.get("/github/repositories/astra-test-user/sample").status_code == 404
        assert another_user.get("/github/repositories/astra-test-user/sample/issues").status_code == 404
        assert another_user.get("/github/activity").json() == []
        assert another_user.get("/github/accounts").json()["accounts"] == []
        assert another_user.get("/github/repositories").json()["repositories"] == []


def test_github_api_requires_configuration_and_authenticated_workspace(auth_client):
    assert auth_client.get("/github/accounts").status_code == 401
    assert register_user(auth_client, "waiting@example.com").status_code == 201
    accounts = auth_client.get("/github/accounts")
    assert accounts.status_code == 200 and accounts.json() == {"configured": False, "accounts": []}
    assert auth_client.get("/github/connect", follow_redirects=False).status_code == 503
    assert auth_client.get("/github/repositories/acme/service/contents", params={"path": "../secrets"}).status_code == 400
