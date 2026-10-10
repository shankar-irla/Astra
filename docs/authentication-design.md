# Authentication and authorization design

## Current state

Registration, login, logout, current identity, and personal-workspace listing now have API endpoints. Passwords use Argon2id hashes. Session values are high-entropy opaque tokens; only SHA-256 digests are stored. Cookies are `HttpOnly`, `SameSite=Lax`, and become `Secure` when `SESSION_COOKIE_SECURE=true`. Mutating auth requests require a double-submit CSRF token and an allowed Origin. Auth attempts are throttled per hashed client IP using database records (20 login attempts per 15 minutes; 10 registrations per hour). Migration 0002 assigns existing users a personal workspace before requiring a workspace on their sessions.

This is an initial account layer, not a production identity provider: email verification, password reset, MFA, session management UI, workspace invitations/role administration, trusted reverse-proxy configuration, and operational anomaly monitoring remain to be implemented. Do not expose other tenant resources before their endpoints enforce membership checks.

## Intended implementation

1. Add email verification, recovery, and session-management controls.
2. Add workspace invitations and role-scoped administrative operations with membership tests.
3. Configure trusted proxies and production rate limits at the ingress; the current API throttles by the ASGI client address and does not trust forwarded headers.
4. Add GitHub App installation webhooks and installation tokens for repository-scoped automation. The account user-authorization path currently encrypts user tokens and revokes them when disconnected.

## Current release gate

The current GitHub read endpoints require an Astra session and select credentials only from the active user's active workspace. Repository, branch, and content access has cross-user test coverage. Apply the same checks and cross-tenant tests to every new task, issue, pull-request, audit, and workspace-management resource before exposing it. Production deployment still requires HTTPS cookies, trusted proxy configuration, email verification/recovery, MFA policy decisions, workspace administration, and operational monitoring.
