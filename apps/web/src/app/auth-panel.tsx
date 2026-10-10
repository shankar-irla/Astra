"use client";

import { FormEvent, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_ASTRA_API_URL ?? "http://localhost:8000";
type Identity = { email: string; workspace_name: string };
type GitHubAccount = { id: string; login: string; avatar_url: string | null };
type Repository = { id: number; full_name: string; private: boolean; html_url: string; description: string | null };

export function AuthPanel() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [mode, setMode] = useState<"login" | "register">("register");
  const [busy, setBusy] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  const [githubConfigured, setGitHubConfigured] = useState(false);
  const [githubAccounts, setGitHubAccounts] = useState<GitHubAccount[]>([]);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [githubBusy, setGitHubBusy] = useState(false);

  async function loadGitHub() {
    const [accountResponse, repoResponse] = await Promise.all([
      fetch(`${API_URL}/github/accounts`, { credentials: "include", cache: "no-store" }),
      fetch(`${API_URL}/github/repositories`, { credentials: "include", cache: "no-store" }),
    ]);
    if (!accountResponse.ok || !repoResponse.ok) throw new Error("Could not load GitHub connection status.");
    const accountData = await accountResponse.json() as { configured: boolean; accounts: GitHubAccount[] };
    const repoData = await repoResponse.json() as { repositories: Repository[]; warnings: { account: string; message: string }[] };
    setGitHubConfigured(accountData.configured);
    setGitHubAccounts(accountData.accounts);
    setRepositories(repoData.repositories);
    if (repoData.warnings.length) setNotice(repoData.warnings.map((warning) => `${warning.account}: ${warning.message}`).join(" "));
  }

  useEffect(() => {
    fetch(`${API_URL}/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => {
        if (response.ok) {
          setIdentity(await response.json());
          return loadGitHub();
        }
        setIdentity(null);
      })
      .catch(() => setNotice("The API is offline. Start it to use account access."))
      .finally(() => setBusy(false));
  }, []);

  async function csrf() {
    const response = await fetch(`${API_URL}/auth/csrf`, { credentials: "include", cache: "no-store" });
    if (!response.ok) throw new Error("Could not start a secure session. Check that the API is running.");
    return (await response.json() as { csrf_token: string }).csrf_token;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setNotice("");
    const data = new FormData(event.currentTarget);
    try {
      const csrfToken = await csrf();
      const response = await fetch(`${API_URL}/auth/${mode}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ email: data.get("email"), password: data.get("password") }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Unable to authenticate.");
      setIdentity({ email: result.email, workspace_name: "Personal workspace" });
      window.dispatchEvent(new Event("astra-auth-change"));
      await loadGitHub();
      setNotice(mode === "register" ? "Your account and workspace are ready." : "You are signed in.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "A network error occurred.");
    } finally {
      setSubmitting(false);
    }
  }

  async function disconnectGitHub(accountId: string) {
    setGitHubBusy(true);
    setNotice("");
    try {
      const csrfToken = await csrf();
      const response = await fetch(`${API_URL}/github/accounts/${accountId}`, {
        method: "DELETE", credentials: "include", headers: { "X-CSRF-Token": csrfToken },
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "Could not disconnect GitHub.");
      await loadGitHub();
      setNotice("GitHub authorization was revoked and disconnected.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "A network error occurred.");
    } finally {
      setGitHubBusy(false);
    }
  }

  async function signOut() {
    setSubmitting(true);
    try {
      const csrfToken = await csrf();
      const response = await fetch(`${API_URL}/auth/logout`, { method: "POST", credentials: "include", headers: { "X-CSRF-Token": csrfToken } });
      if (!response.ok) throw new Error("Could not sign out. Try again.");
      setIdentity(null);
      window.dispatchEvent(new Event("astra-auth-change"));
      setNotice("You have signed out.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "A network error occurred.");
    } finally {
      setSubmitting(false);
    }
  }

  if (busy) return <p className="auth-loading" role="status">Checking your session…</p>;
  if (identity) return (
    <div className="signed-in-stack">
      <div className="auth-signed-in">
        <div className="auth-user-mark" aria-hidden="true">{identity.email.slice(0, 1).toUpperCase()}</div>
        <div className="auth-identity"><strong>{identity.email}</strong><span>{identity.workspace_name}</span></div>
        <button className="text-button" onClick={signOut} disabled={submitting}>{submitting ? "Working…" : "Sign out"}</button>
      </div>
      {githubAccounts.length === 0 ? (
        <div className="github-connect-card">
          <div className="github-connect-heading"><span className="github-mark" aria-hidden="true">⌘</span><div><strong>GitHub account</strong><span>Connect an account you control.</span></div></div>
          {githubConfigured ? <a className="github-connect-button" href={`${API_URL}/github/connect`}>Connect GitHub <span aria-hidden="true">↗</span></a> : <p className="integration-pending">GitHub App credentials and an encryption key are needed to enable authorization.</p>}
        </div>
      ) : (
        <div className="github-connected-card">
          <div className="github-connect-heading"><span className="github-mark connected" aria-hidden="true">✓</span><div><strong>Connected as {githubAccounts[0].login}</strong><span>Repositories authorized by GitHub</span></div></div>
          <button className="text-button disconnect-button" disabled={githubBusy} onClick={() => disconnectGitHub(githubAccounts[0].id)}>{githubBusy ? "Disconnecting…" : "Disconnect"}</button>
          <div className="repo-preview">{repositories.length ? repositories.slice(0, 3).map((repo) => <a key={repo.id} href={repo.html_url} target="_blank" rel="noreferrer"><span>{repo.full_name}</span><small>{repo.private ? "PRIVATE" : "PUBLIC"}</small></a>) : <span className="integration-pending">No repositories returned for this account.</span>}</div>
        </div>
      )}
      {notice && <p className="auth-notice" role="status">{notice}</p>}
    </div>
  );

  return (
    <div className="auth-box">
      <div className="auth-tabs" role="tablist" aria-label="Account access">
        <button role="tab" aria-selected={mode === "register"} className={mode === "register" ? "selected" : ""} onClick={() => { setMode("register"); setNotice(""); }}>Create account</button>
        <button role="tab" aria-selected={mode === "login"} className={mode === "login" ? "selected" : ""} onClick={() => { setMode("login"); setNotice(""); }}>Sign in</button>
      </div>
      <form className="auth-form" onSubmit={submit}>
        <label>Email<input name="email" type="email" autoComplete="email" maxLength={320} required placeholder="you@example.com" /></label>
        <label>Password<input name="password" type="password" autoComplete={mode === "register" ? "new-password" : "current-password"} minLength={12} maxLength={128} required placeholder="At least 12 characters" /></label>
        <button className="primary-button" type="submit" disabled={submitting}>{submitting ? "Please wait…" : mode === "register" ? "Create secure account" : "Sign in securely"}<span aria-hidden="true">→</span></button>
      </form>
      <p className="auth-assurance"><span aria-hidden="true">⌑</span> Passwords are stored as Argon2 hashes. Sessions use secure, HTTP-only cookies.</p>
      {notice && <p className="auth-notice" role="status">{notice}</p>}
    </div>
  );
}
