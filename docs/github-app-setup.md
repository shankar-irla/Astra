# GitHub App setup

## Implemented connection path

Astra uses a GitHub App user authorization flow for account-specific operations. The authorization state is one-use and session-bound, uses PKCE S256, and has a ten-minute lifetime. Access and refresh tokens are encrypted with Fernet before persistence. The current API supports account connect/disconnect, authorized repository listing and details, branch listing, bounded recent commit history, issues and pull-request views, directory navigation, bounded UTF-8 file previews, explicit issue creation, and an activity feed for recorded writes. The app does not use a shared personal access token.

GitHub App user tokens can be configured to expire and use rotating refresh tokens. Enable token expiration in the GitHub App and give the app only the repository permissions needed by the current operations. For repository details, branches, commits, and file browsing, grant repository metadata read access and contents read access. For the repository-level Issues and Pull requests tabs, grant Issues read and Pull requests read. The create-issue form also requires Issues write. Add other write permissions only alongside their reviewed feature and approval path.

## Local configuration

1. Register a GitHub App in your GitHub account or organization settings.
2. Set its user authorization callback URL to `http://localhost:8000/github/callback` for this local setup. Use an exact HTTPS callback URL in deployed environments; do not enable wildcard callback matching.
3. Set the app homepage URL to the Astra web URL. Review the permissions and enable user authorization.
4. Generate a Fernet key, store it in a secret manager, and back it up securely. Loss of this key makes stored credentials unreadable; rotate it by decrypting and re-encrypting credentials under a controlled migration.
5. Set `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_CALLBACK_URL`, and `TOKEN_ENCRYPTION_KEY` in `.env`. Set `FRONTEND_URL` to the exact web origin.
6. Restart the API and web app. Sign in to Astra, then choose **Connect GitHub**. Review the GitHub permission prompt before authorizing.

Example key-generation command:

```sh
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

For a deployment, use separate GitHub App registrations and encryption keys for development, staging, and production. Do not commit credentials. GitHub connection status, disconnect, OAuth callback replay protection, repository listing and details, branch browsing, commit history, and file browsing have mocked integration coverage; an actual GitHub App/account has not been configured or verified in this workspace.

## Not implemented yet

GitHub App installation webhooks and installation-token operations are not wired. The current user-token path is account-scoped. Issue creation is supported with explicit submission and persistent idempotency/audit records. Repository creation, file writes, issue editing/comments, pull-request writes, and branch/commit mutations remain unimplemented. Repository code execution remains disabled until a properly isolated worker is available.
