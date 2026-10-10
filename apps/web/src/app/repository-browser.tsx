"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_ASTRA_API_URL ?? "http://localhost:8000";
type Repository = { id: number; full_name: string; private: boolean; default_branch?: string; account: string };
type Branch = { name: string; protected: boolean; commit_sha: string | null };
type Entry = { name: string; path: string; type: string; size: number; html_url: string | null };
type Listing = { type: "directory" | "file"; path: string; entries?: Entry[]; name?: string; content?: string | null; encoding?: string; size?: number };
type RepositoryDetail = { description: string | null; language: string | null; default_branch: string; private: boolean; archived: boolean; html_url: string; stargazers_count: number; forks_count: number; open_issues_count: number; license: string | null; permissions: { admin: boolean; maintain: boolean; push: boolean; triage: boolean; pull: boolean } };
type Commit = { sha: string; short_sha: string; message: string; author: string; date: string | null; html_url: string | null };
type Issue = { number: number; title: string; state: string; author: string | null; created_at: string | null; updated_at: string | null; comments: number; html_url: string | null; body: string; labels: { name: string; color: string | null }[] };

function GitHubItemCard({ item }: { item: Issue }) {
  return (
    <article className="github-item">
      <div className="github-item-top">
        <span className={item.state === "open" ? "issue-state open" : "issue-state closed"}>{item.state}</span>
        <small>#{item.number}</small>
        {item.html_url && <a href={item.html_url} target="_blank" rel="noreferrer">Open ↗</a>}
      </div>
      <h3>{item.title}</h3>
      <p>{item.body || "No description provided."}</p>
      <div className="github-item-foot">
        <span>by {item.author || "unknown"}</span>
        <span>{item.comments} comments</span>
        <span>{item.created_at ? new Date(item.created_at).toLocaleDateString() : "Date unavailable"}</span>
      </div>
      {item.labels.length > 0 && <div className="github-labels">
        {item.labels.map((label) => <span key={label.name} style={label.color ? { borderColor: `#${label.color}`, color: `#${label.color}` } : undefined}>{label.name}</span>)}
      </div>}
    </article>
  );
}

export function RepositoryBrowser() {
  const [ready, setReady] = useState(false);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [selected, setSelected] = useState("");
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branch, setBranch] = useState("");
  const [listing, setListing] = useState<Listing | null>(null);
  const [path, setPath] = useState("");
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<RepositoryDetail | null>(null);
  const [commits, setCommits] = useState<Commit[]>([]);
  const [view, setView] = useState<"source" | "issues" | "pulls">("source");
  const [itemState, setItemState] = useState<"open" | "closed" | "all">("open");
  const [items, setItems] = useState<Issue[]>([]);
  const [issueTitle, setIssueTitle] = useState("");
  const [issueBody, setIssueBody] = useState("");
  const [issueKey, setIssueKey] = useState("");
  const [issueNotice, setIssueNotice] = useState("");
  const [issueSubmitting, setIssueSubmitting] = useState(false);
  const [itemRefresh, setItemRefresh] = useState(0);
  const [viewError, setViewError] = useState("");
  const [sourceError, setSourceError] = useState("");
  const [message, setMessage] = useState("Connect GitHub to browse authorized repositories.");
  const [busy, setBusy] = useState(false);
  const currentRepo = useMemo(() => repositories.find((repo) => repo.full_name === selected), [repositories, selected]);
  const filteredRepositories = useMemo(() => repositories.filter((repo) => repo.full_name.toLowerCase().includes(search.trim().toLowerCase())), [repositories, search]);

  useEffect(() => {
    let active = true;
    const load = () => {
      fetch(`${API_URL}/auth/me`, { credentials: "include", cache: "no-store" }).then(async (response) => {
        if (!active) return;
        if (!response.ok) { setReady(false); setRepositories([]); setSelected(""); return; }
        setReady(true);
        const result = await fetch(`${API_URL}/github/repositories`, { credentials: "include", cache: "no-store" });
        if (!result.ok) throw new Error("GitHub repositories could not be loaded.");
        const data = await result.json() as { repositories: Repository[] };
        if (!active) return;
        setRepositories(data.repositories);
        if (data.repositories.length) setSelected(data.repositories[0].full_name);
        else setMessage("No repositories are available. Connect a GitHub account to begin.");
      }).catch((error: unknown) => {
        if (active) setMessage(error instanceof Error ? error.message : "The API is unavailable.");
      });
    };
    window.addEventListener("astra-auth-change", load);
    load();
    return () => { active = false; window.removeEventListener("astra-auth-change", load); };
  }, []);

  useEffect(() => {
    if (!currentRepo || view !== "source") return;
    let active = true;
    setSourceError("");
    setBusy(true); setListing(null); setPath(""); setBranches([]); setBranch("");
    const [owner, name] = currentRepo.full_name.split("/");
    fetch(`${API_URL}/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/branches?account=${encodeURIComponent(currentRepo.account)}`, { credentials: "include", cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("Branches could not be loaded. Reconnect GitHub if its authorization expired.");
        const result = await response.json() as Branch[];
        if (!active) return;
        setBranches(result);
        const preferred = result.some((item) => item.name === currentRepo.default_branch) ? currentRepo.default_branch! : result[0]?.name;
        if (preferred) setBranch(preferred);
        else setMessage("This repository has no branches to browse.");
      }).catch((error: unknown) => { if (active) setSourceError(error instanceof Error ? error.message : "Branches unavailable."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [currentRepo, view]);

  useEffect(() => {
    if (!currentRepo || view !== "source") return;
    let active = true;
    setDetail(null); setCommits([]);
    setSourceError("");
    const [owner, name] = currentRepo.full_name.split("/");
    const account = encodeURIComponent(currentRepo.account);
    const branchQuery = branch ? `&branch=${encodeURIComponent(branch)}` : "";
    Promise.all([
      fetch(`${API_URL}/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}?account=${account}`, { credentials: "include", cache: "no-store" }),
      fetch(`${API_URL}/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/commits?account=${account}${branchQuery}`, { credentials: "include", cache: "no-store" }),
    ]).then(async ([detailResponse, commitResponse]) => {
      if (!detailResponse.ok || !commitResponse.ok) throw new Error("Repository details or recent commits could not be loaded.");
      const [detailData, commitData] = await Promise.all([detailResponse.json(), commitResponse.json()]);
      if (!active) return;
      setDetail(detailData as RepositoryDetail);
      setCommits(commitData as Commit[]);
    }).catch((error: unknown) => { if (active) setSourceError(error instanceof Error ? error.message : "Repository details are unavailable."); });
    return () => { active = false; };
  }, [currentRepo, branch, view]);

  useEffect(() => {
    if (!currentRepo || view === "source") return;
    let active = true;
    setViewError("");
    setBusy(true); setItems([]);
    const [owner, name] = currentRepo.full_name.split("/");
    const query = new URLSearchParams({ account: currentRepo.account, state: itemState, limit: "30" });
    fetch(`${API_URL}/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/${view}?${query}`, { credentials: "include", cache: "no-store" })
      .then(async (response) => {
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail ?? `Could not load ${view}. Check the GitHub App permission and reconnect.`);
        if (active) setItems(result as Issue[]);
      }).catch((error: unknown) => { if (active) setViewError(error instanceof Error ? error.message : `Could not load ${view}.`); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [currentRepo, view, itemState, itemRefresh]);

  async function createIssue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!currentRepo) return;
    setIssueSubmitting(true); setIssueNotice("");
    const key = issueKey || crypto.randomUUID();
    setIssueKey(key);
    const [owner, name] = currentRepo.full_name.split("/");
    try {
      const csrfResponse = await fetch(`${API_URL}/auth/csrf`, { credentials: "include", cache: "no-store" });
      if (!csrfResponse.ok) throw new Error("Could not start a secure request. Sign in again and retry.");
      const { csrf_token: csrfToken } = await csrfResponse.json() as { csrf_token: string };
      const response = await fetch(`${API_URL}/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/issues?account=${encodeURIComponent(currentRepo.account)}`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, "Idempotency-Key": key },
        body: JSON.stringify({ title: issueTitle, body: issueBody }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail ?? "GitHub could not create the issue.");
      setIssueTitle(""); setIssueBody(""); setIssueKey("");
      setIssueNotice(`Issue #${result.number} created on GitHub.`);
      window.dispatchEvent(new Event("astra-activity-change"));
      setItemRefresh((value) => value + 1);
    } catch (error) {
      setIssueNotice(error instanceof Error ? error.message : "The request failed. Check the repository before retrying.");
    } finally {
      setIssueSubmitting(false);
    }
  }

  useEffect(() => {
    if (!currentRepo || !branch || view !== "source") return;
    let active = true;
    setBusy(true); setListing(null);
    const [owner, name] = currentRepo.full_name.split("/");
    const query = new URLSearchParams({ ref: branch, path, account: currentRepo.account });
    fetch(`${API_URL}/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/contents?${query}`, { credentials: "include", cache: "no-store" })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail ?? "Repository contents could not be loaded.");
        if (active) setListing(data as Listing);
      }).catch((error: unknown) => { if (active) setSourceError(error instanceof Error ? error.message : "Repository contents unavailable."); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [currentRepo, branch, path, view]);

  if (!ready && message.startsWith("Connect GitHub")) return null;
  const folders = (listing?.entries ?? []).filter((entry) => entry.type === "dir");
  const files = (listing?.entries ?? []).filter((entry) => entry.type !== "dir");
  const crumbs = path ? path.split("/").map((part, index, all) => ({ part, path: all.slice(0, index + 1).join("/") })) : [];

  return <section className="repository-browser" aria-label="Repository browser">
    <div className="browser-heading"><div><span className="card-kicker">AUTHORIZED SOURCE</span><h2>Repository browser</h2><p>Read branches and files from repositories you have authorized.</p></div><span className="read-only-tag">READ ONLY</span></div>
    {!ready || !repositories.length ? <p className="browser-message">{message}</p> : <>
      <div className="browser-controls">
        <label>Find repository<input className="repository-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search authorized repositories" /></label>
        <label>Repository<select value={selected} onChange={(event) => { setSelected(event.target.value); setIssueTitle(""); setIssueBody(""); setIssueKey(""); setIssueNotice(""); setSourceError(""); setViewError(""); }} aria-label="Select repository">{filteredRepositories.map((repo) => <option key={`${repo.account}:${repo.id}`} value={repo.full_name}>{repo.full_name}{repo.private ? " · private" : ""}</option>)}</select></label>
        {view === "source" ? <label>Branch<select value={branch} onChange={(event) => setBranch(event.target.value)}>{branches.map((item) => <option key={item.name} value={item.name}>{item.name}{item.protected ? " · protected" : ""}</option>)}</select></label> : <label>State<select value={itemState} onChange={(event) => setItemState(event.target.value as "open" | "closed" | "all")}><option value="open">Open</option><option value="closed">Closed</option><option value="all">All</option></select></label>}
      </div>
      {!filteredRepositories.length && <p className="browser-message">No repositories match your search.</p>}
      <div className="browser-tabs" role="tablist" aria-label="Repository views">{([["source", "Code"], ["issues", "Issues"], ["pulls", "Pull requests"]] as const).map(([value, label]) => <button key={value} className={view === value ? "browser-tab selected" : "browser-tab"} role="tab" aria-selected={view === value} onClick={() => setView(value)}>{label}</button>)}</div>
      {view === "source" && <>
        {sourceError && <p className="browser-error" role="alert">{sourceError}</p>}
        {detail && <div className="repository-summary"><div className="repository-summary-title"><strong>{selected}</strong><a href={detail.html_url} target="_blank" rel="noreferrer">Open on GitHub ↗</a></div><p>{detail.description || "No description provided."}</p><div className="repository-facts"><span>{detail.language || "Language not set"}</span><span>{detail.private ? "Private" : "Public"}</span><span>{detail.stargazers_count.toLocaleString()} stars</span><span>{detail.forks_count.toLocaleString()} forks</span><span>{detail.open_issues_count.toLocaleString()} open issues</span><span>{detail.license || "No license detected"}</span><span>Access: {detail.permissions.pull ? "read" : "none"}{detail.permissions.push ? " · write" : ""}{detail.permissions.admin ? " · admin" : ""}</span>{detail.archived && <span>Archived</span>}</div></div>}
        {detail && <div className="repository-commits"><div className="repository-subheading"><strong>Recent commits</strong><span>{branch || detail.default_branch}</span></div>{commits.length ? commits.map((commit) => <div className="commit-row" key={commit.sha}><span className="commit-mark" aria-hidden="true">↳</span><div><strong>{commit.message}</strong><small>{commit.author} · {commit.date ? new Date(commit.date).toLocaleString() : "Date unavailable"}</small></div>{commit.html_url && <a href={commit.html_url} target="_blank" rel="noreferrer" aria-label={`Open commit ${commit.short_sha} on GitHub`}>{commit.short_sha}</a>}</div>) : <p className="browser-message">No commits were returned for this branch.</p>}</div>}
        <div className="browser-path"><button onClick={() => setPath("")} aria-label="Repository root">⌂</button>{crumbs.map((crumb) => <span key={crumb.path}><i>/</i><button onClick={() => setPath(crumb.path)}>{crumb.part}</button></span>)}</div>
        {busy && <p className="browser-message" role="status">Loading repository contents…</p>}
        {!busy && listing?.type === "directory" && <div className="file-list">
          {path && <button className="file-row parent-row" onClick={() => setPath(path.split("/").slice(0, -1).join("/"))}><span>↰</span><strong>..</strong><small>Parent directory</small></button>}
          {folders.map((entry) => <button className="file-row" key={entry.path} onClick={() => setPath(entry.path)}><span>▰</span><strong>{entry.name}</strong><small>Folder</small></button>)}
          {files.map((entry) => <button className="file-row" key={entry.path} onClick={() => setPath(entry.path)}><span>▤</span><strong>{entry.name}</strong><small>{entry.size.toLocaleString()} B</small></button>)}
          {!folders.length && !files.length && <p className="browser-message">This directory is empty.</p>}
        </div>}
        {!busy && listing?.type === "file" && <div className="file-preview"><div className="file-preview-title"><strong>{listing.name}</strong><span>{listing.encoding === "utf-8" ? `${(listing.size ?? 0).toLocaleString()} bytes` : "Binary file"}</span></div>{listing.encoding === "utf-8" ? <pre>{listing.content}</pre> : <p>Preview is available for UTF-8 text files up to 1 MB.</p>}</div>}
      </>}
      {view !== "source" && (
        <div className="github-item-list">
          {view === "issues" && (
            <form className="issue-create-form" onSubmit={createIssue}>
              <div><strong>Create an issue</strong><span>This action creates a real issue in the selected GitHub repository.</span></div>
              <label>Title<input value={issueTitle} onChange={(event) => { setIssueTitle(event.target.value); setIssueKey(""); }} maxLength={256} required placeholder="Summarize the issue" /></label>
              <label>Description<textarea value={issueBody} onChange={(event) => { setIssueBody(event.target.value); setIssueKey(""); }} maxLength={65536} rows={4} placeholder="Context, steps to reproduce, or expected behavior" /></label>
              <button type="submit" disabled={issueSubmitting}>{issueSubmitting ? "Creating…" : "Create issue on GitHub"}<span aria-hidden="true">→</span></button>
              {issueNotice && <p role="status">{issueNotice}</p>}
            </form>
          )}
          {viewError && !busy && <p className="browser-error" role="alert">{viewError}</p>}
          {busy ? <p className="browser-message" role="status">Loading {view === "issues" ? "issues" : "pull requests"}…</p>
            : viewError ? null
              : items.length ? items.map((item) => <GitHubItemCard item={item} key={item.number} />)
                : <p className="browser-message">No {view === "issues" ? "issues" : "pull requests"} in this state.</p>}
        </div>
      )}
    </>}
  </section>;
}
