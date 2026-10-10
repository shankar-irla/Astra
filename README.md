# Astra

Astra is an AI-assisted GitHub engineering platform. It provides account and personal-workspace identity, secure sessions, an optional GitHub App authorization flow, workspace-scoped repository/branch/file browsing and issue creation, a premium light/dark dashboard, and persisted AI task planning. Agent planning is available only when OpenAI and GitHub are configured. Repository code changes and task execution are not enabled.

## Architecture

```mermaid
flowchart LR
  Browser[Next.js overview] -->|server-side readiness check| API[FastAPI]
  API --> DB[(PostgreSQL)]
  API --> GH[GitHub REST API]
  API --> Queue[Redis + worker - planned]
  Queue --> Isolated[Isolated task worker - planned]
```

The API exposes account-scoped GitHub connection status, searchable repository listing, repository details, branches, bounded commit history, issue and pull-request views, UTF-8 file previews, explicit issue creation, and authenticated task planning. GitHub credentials are encrypted at rest. A planning request requires explicit consent; Astra sends the request, repository metadata, and at most 8 KB of README text to the configured OpenAI model, stores the resulting plan in the workspace database, and sets provider response storage off. Planning does not change repository contents.

## Phased checklist

- [x] Milestone 1 foundation: FastAPI app, configuration, health/readiness, database connectivity, Alembic migrations, Next.js dashboard, tests, local setup docs.
- [x] CI workflow for API tests/migration checks and web lint/build.
- [x] Identity slice: Argon2 password hashes, CSRF-protected login/register/logout, opaque HTTP-only sessions, personal workspaces, DB-backed throttling.
- [x] GitHub account slice: session-bound PKCE authorization state, encrypted user tokens, disconnect/revocation, authorized repository listing.
- [ ] Milestone 1 completion: email recovery/verification, workspace membership administration, MFA, and further tenant-boundary coverage.
- [x] Phase 2 read-only slice: GitHub App user authorization, searchable authorized repositories, details, branches, recent commits, issues, pull requests, folders, and bounded text previews.
- [x] Phase 2 issue-write slice: explicit issue creation, CSRF protection, persisted idempotency, request fingerprinting, durable mutation audit, and workspace-scoped activity feed.
- [ ] Remaining Phase 2 writes: GitHub App installation/webhooks, repository creation, safe Git mutations, issue editing/comments, and branch/PR workflows.
- [x] Phase 3 planning slice: consent-gated structured plan generation, validated bounded dependency graph, persisted plan/history/events, and workspace-scoped API/UI with one-time approve/reject review decisions.
- [ ] Phase 3 execution slice: typed tool registry and isolated worker; code execution stays disabled until secure isolation exists.
- [ ] Phase 4: reviewed patch, feature branch, approval, PR creation and CI status.
- [ ] Phase 5: webhook ingestion, durable queue, workers and persisted live task events.
- [ ] Phase 6–7: tenant isolation, operational limits, threat-model review, deployment and hardening.

## Local setup

Requirements: Python 3.12 or 3.13, Node.js 20.9+, npm, and Docker Compose (for PostgreSQL). SQLite is the default, so PostgreSQL is optional for the initial slice.

```sh
cp .env.example .env
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r services/api/requirements.lock
python -m pip install --no-deps -e services/api
.venv/bin/alembic -c services/api/alembic.ini upgrade head
uvicorn app.main:app --app-dir services/api --reload
```

In another terminal:

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

In a third terminal, start the web app. Its API readiness request runs server-side; `ASTRA_API_URL` is not exposed to browser JavaScript.

```sh
cd apps/web
npm ci
ASTRA_API_URL=http://127.0.0.1:8000 npm run dev
```

Open `http://localhost:3000`. Keep the `localhost` hostname for both services so the browser session cookie works across the web/API origins. The overview displays live API/database readiness, account access, and a repository browser after GitHub App credentials are configured and an account is connected. The theme toggle switches between premium dark and soft light palettes and remembers the selection in the browser.

To use PostgreSQL, start it with `docker compose up -d db`, then set `DATABASE_URL=postgresql+psycopg://astra:astra@localhost:5432/astra` in `.env`. Stop it with `docker compose down`; add `-v` only if you intend to remove the local database volume.

Run backend tests and build the web app:

```sh
python -m pytest services/api/tests
npm --prefix apps/web run build
```

## GitHub configuration and current limitation

The GitHub App user-authorization flow supports account connection, refreshable encrypted credentials, disconnect/revocation, and paginated authorized-repository listing. Configure the App using [`docs/github-app-setup.md`](docs/github-app-setup.md). No real App credentials or GitHub account are configured here, so real access remains unverified. Do not put tokens in `.env`, browser storage, URLs, or logs.

Read the current security design in [`docs/authentication-design.md`](docs/authentication-design.md) and the backend boundaries in [`docs/architecture.md`](docs/architecture.md).

The GitHub API routes require an Astra session and scope connected accounts to the active user's workspace. A shared token endpoint is intentionally absent because it would break user and tenant isolation.

## AI task planning

Set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` in `.env` to enable planning. The planner sends the task request, selected repository metadata, and up to 8 KB from the selected branch's root `README.md` only after the user checks the consent box. Astra persists the request and returned plan in the active workspace. The response is requested with provider storage disabled. Do not include secrets in the task request or README. Plan output is untrusted advice and requires human review; Astra records an approve/reject decision once. That review does not run code or authorize a later mutation. There is no execution, branch creation, patch generation, or PR submission yet.

## Current limitations

- Automation, Integrations management, and Settings pages are not implemented yet. The AI Engineer panel supports task planning and history; issue editing/comments and pull-request writes remain unimplemented.
- Registration, login, logout, hashed opaque sessions, CSRF validation, workspace membership, and persisted auth throttling are implemented. Email recovery/verification, MFA, workspace administration, and trusted proxy configuration remain.
- GitHub account connection requires a registered GitHub App and encryption key; see [`docs/github-app-setup.md`](docs/github-app-setup.md). This workspace has not connected a real GitHub account, so external GitHub browsing has only been tested with mocked API responses.
- PostgreSQL connectivity is configurable, but SQLite is used by default. The readiness check validates connectivity only.
- The GitHub service seam is tested with mocked HTTP responses; real GitHub access has not been configured or verified.
- No task execution, webhook receiver, durable queue, isolated worker, review/approval workflow, or code changes exist yet. Code execution remains disabled until an isolation design is implemented and audited.
- The requested goandgrab.pt reference was not reachable in the browser lookup. This is an original premium light/dark dashboard, not a verified reproduction of that site.
