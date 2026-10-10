from typing import Any
import base64
import binascii
from urllib.parse import quote, urlsplit

import httpx


class GitHubAPIError(Exception):
    """A safe, user-facing GitHub integration error."""


class GitHubClient:
    """Small GitHub REST boundary. Tokens are supplied per request and never persisted."""

    def __init__(self, api_url: str, api_version: str, timeout_seconds: float = 10) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_version = api_version
        self.timeout_seconds = timeout_seconds

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": self.api_version,
        }

    async def list_branches(self, access_token: str, owner: str, repository: str) -> list[dict[str, Any]]:
        url = f"{self.api_url}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/branches"
        branches: list[dict[str, Any]] = []
        params: dict[str, str | int] | None = {"per_page": 100}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                for _ in range(10):
                    response = await client.get(url, headers=self._headers(access_token), params=params)
                    if response.status_code == 401:
                        raise GitHubAPIError("GitHub rejected the credential. Reconnect the account.")
                    if response.status_code == 403:
                        raise GitHubAPIError("GitHub denied access or the API rate limit was reached.")
                    if response.status_code == 404:
                        raise GitHubAPIError("Repository or branch data was not found.")
                    response.raise_for_status()
                    page = response.json()
                    if not isinstance(page, list):
                        raise GitHubAPIError("GitHub returned an unexpected branch response.")
                    branches.extend(page)
                    next_url = response.links.get("next", {}).get("url")
                    if not next_url:
                        return branches
                    expected, candidate = urlsplit(self.api_url), urlsplit(next_url)
                    if (candidate.scheme, candidate.netloc) != (expected.scheme, expected.netloc):
                        raise GitHubAPIError("GitHub returned an unsafe pagination link.")
                    url, params = next_url, None
            raise GitHubAPIError("Branch listing exceeded the pagination safety limit.")
        except GitHubAPIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubAPIError("GitHub is temporarily unavailable. Try again later.") from exc

    async def get_repository(self, access_token: str, owner: str, repository: str) -> dict[str, Any]:
        url = f"{self.api_url}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                response = await client.get(url, headers=self._headers(access_token))
            if response.status_code == 401:
                raise GitHubAPIError("GitHub rejected the credential. Reconnect the account.")
            if response.status_code == 403:
                raise GitHubAPIError("GitHub denied access or the API rate limit was reached.")
            if response.status_code == 404:
                raise GitHubAPIError("Repository was not found.")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("full_name"), str):
                raise GitHubAPIError("GitHub returned an unexpected repository response.")
            return payload
        except GitHubAPIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubAPIError("GitHub is temporarily unavailable. Try again later.") from exc

    async def list_commits(self, access_token: str, owner: str, repository: str,
                           branch: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        url = f"{self.api_url}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/commits"
        params: dict[str, str | int] | None = {"per_page": min(max(limit, 1), 100)}
        if branch:
            params["sha"] = branch
        commits: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                for _ in range(5):
                    response = await client.get(url, headers=self._headers(access_token), params=params)
                    if response.status_code == 401:
                        raise GitHubAPIError("GitHub rejected the credential. Reconnect the account.")
                    if response.status_code == 403:
                        raise GitHubAPIError("GitHub denied access or the API rate limit was reached.")
                    if response.status_code == 404:
                        raise GitHubAPIError("Repository or commit history was not found.")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                        raise GitHubAPIError("GitHub returned an unexpected commit response.")
                    commits.extend(payload)
                    if len(commits) >= limit:
                        return commits[:limit]
                    next_url = response.links.get("next", {}).get("url")
                    if not next_url:
                        return commits
                    expected, candidate = urlsplit(self.api_url), urlsplit(next_url)
                    if (candidate.scheme, candidate.netloc) != (expected.scheme, expected.netloc):
                        raise GitHubAPIError("GitHub returned an unsafe pagination link.")
                    url, params = next_url, None
            return commits[:limit]
        except GitHubAPIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubAPIError("GitHub is temporarily unavailable. Try again later.") from exc

    async def list_repository_items(self, access_token: str, owner: str, repository: str,
                                    resource: str, state: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        if resource not in {"issues", "pulls"}:
            raise ValueError("Unsupported repository resource.")
        url = f"{self.api_url}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/{resource}"
        params: dict[str, str | int] | None = {"state": state, "per_page": min(max(limit, 1), 100)}
        records: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                for _ in range(5):
                    response = await client.get(url, headers=self._headers(access_token), params=params)
                    if response.status_code == 401:
                        raise GitHubAPIError("GitHub rejected the credential. Reconnect the account.")
                    if response.status_code == 403:
                        raise GitHubAPIError("GitHub denied access or the API rate limit was reached.")
                    if response.status_code == 404:
                        raise GitHubAPIError("Repository resource was not found.")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                        raise GitHubAPIError("GitHub returned an unexpected repository resource response.")
                    records.extend(payload)
                    if len(records) >= limit:
                        return records[:limit]
                    next_url = response.links.get("next", {}).get("url")
                    if not next_url:
                        return records
                    expected, candidate = urlsplit(self.api_url), urlsplit(next_url)
                    if (candidate.scheme, candidate.netloc) != (expected.scheme, expected.netloc):
                        raise GitHubAPIError("GitHub returned an unsafe pagination link.")
                    url, params = next_url, None
            return records[:limit]
        except GitHubAPIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubAPIError("GitHub is temporarily unavailable. Try again later.") from exc

    async def create_issue(self, access_token: str, owner: str, repository: str,
                           title: str, body: str = "", labels: list[str] | None = None) -> dict[str, Any]:
        url = f"{self.api_url}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/issues"
        payload = {"title": title, "body": body, "labels": labels or []}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                response = await client.post(url, headers=self._headers(access_token), json=payload)
            if response.status_code == 401:
                raise GitHubAPIError("GitHub rejected the credential. Reconnect the account.")
            if response.status_code == 403:
                raise GitHubAPIError("GitHub denied issue creation or the API rate limit was reached.")
            if response.status_code == 404:
                raise GitHubAPIError("Repository was not found or the account cannot access it.")
            if response.status_code == 422:
                raise GitHubAPIError("GitHub rejected the issue fields. Check labels and repository policy.")
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get("number"), int) or not isinstance(result.get("html_url"), str):
                raise GitHubAPIError("GitHub returned an unexpected issue response.")
            return result
        except GitHubAPIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubAPIError("GitHub is temporarily unavailable. Check the repository before retrying.") from exc

    async def get_contents(self, access_token: str, owner: str, repository: str,
                           path: str = "", ref: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        url = f"{self.api_url}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}/contents"
        if path:
            url += "/" + quote(path, safe="/")
        params = {"ref": ref} if ref else None
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                response = await client.get(url, headers=self._headers(access_token), params=params)
            if response.status_code == 401:
                raise GitHubAPIError("GitHub rejected the credential. Reconnect the account.")
            if response.status_code == 403:
                raise GitHubAPIError("GitHub denied access or the API rate limit was reached.")
            if response.status_code == 404:
                raise GitHubAPIError("Repository path was not found.")
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list) and all(isinstance(entry, dict) for entry in payload):
                return payload
            if isinstance(payload, dict) and isinstance(payload.get("type"), str):
                if payload.get("size", 0) > 1_000_000:
                    raise GitHubAPIError("Files over 1 MB are not available in this browser yet.")
                encoded = payload.get("content", "")
                if payload.get("encoding") == "base64" and isinstance(encoded, str):
                    try:
                        payload["text_content"] = base64.b64decode(encoded, validate=False).decode("utf-8")
                    except (ValueError, UnicodeDecodeError, binascii.Error):
                        payload["text_content"] = None
                else:
                    payload["text_content"] = None
                return payload
            raise GitHubAPIError("GitHub returned an unexpected repository content response.")
        except GitHubAPIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubAPIError("GitHub is temporarily unavailable. Try again later.") from exc

    async def list_repositories(self, access_token: str) -> list[dict[str, Any]]:
        if not access_token:
            raise GitHubAPIError("A GitHub credential is required.")
        repositories: list[dict[str, Any]] = []
        url = f"{self.api_url}/user/repos"
        params: dict[str, str | int] | None = {"per_page": 100, "sort": "updated"}
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": self.api_version,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                for _ in range(10):
                    response = await client.get(url, headers=headers, params=params)
                    if response.status_code == 401:
                        raise GitHubAPIError("GitHub rejected the credential. Reconnect the account.")
                    if response.status_code == 403:
                        raise GitHubAPIError("GitHub denied access or the API rate limit was reached.")
                    if response.status_code == 404:
                        raise GitHubAPIError("The requested GitHub resource was not found.")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, list):
                        raise GitHubAPIError("GitHub returned an unexpected repository response.")
                    repositories.extend(payload)
                    next_url = response.links.get("next", {}).get("url")
                    if not next_url:
                        return repositories
                    expected = urlsplit(self.api_url)
                    candidate = urlsplit(next_url)
                    if (candidate.scheme, candidate.netloc) != (expected.scheme, expected.netloc):
                        raise GitHubAPIError("GitHub returned an unsafe pagination link.")
                    url, params = next_url, None
            raise GitHubAPIError("Repository listing exceeded the pagination safety limit.")
        except GitHubAPIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubAPIError("GitHub is temporarily unavailable. Try again later.") from exc

    async def get_authenticated_user(self, access_token: str) -> dict[str, Any]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {access_token}",
            "X-GitHub-Api-Version": self.api_version,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                response = await client.get(f"{self.api_url}/user", headers=headers)
            if response.status_code == 401:
                raise GitHubAPIError("GitHub rejected the credential. Reconnect the account.")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("id"), int) or not isinstance(payload.get("login"), str):
                raise GitHubAPIError("GitHub returned an unexpected user response.")
            return payload
        except GitHubAPIError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise GitHubAPIError("GitHub is temporarily unavailable. Try again later.") from exc
