"use client";

import { FormEvent, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_ASTRA_API_URL ?? "http://localhost:8000";
type Repo = { full_name: string; account: string; default_branch?: string };
type Branch = { name: string };
type Task = { id: string; repository: string; branch: string; task_type: string; request: string; status: string; model: string | null; failure_reason: string | null; execution_available: false; plan: null | { summary: string; risks: string[]; steps: { id: string; title: string; description: string; dependencies: string[]; verification: string }[]; acceptance_criteria: string[]; verification_plan: string[] } };

export function TaskPlanner() {
  const [repos, setRepos] = useState<Repo[]>([]); const [repo, setRepo] = useState(""); const [branches, setBranches] = useState<Branch[]>([]); const [branch, setBranch] = useState("");
  const [kind, setKind] = useState("feature"); const [request, setRequest] = useState(""); const [consent, setConsent] = useState(false); const [busy, setBusy] = useState(false); const [reviewBusy, setReviewBusy] = useState(false); const [error, setError] = useState(""); const [task, setTask] = useState<Task | null>(null); const [history, setHistory] = useState<Task[]>([]); const [signedIn, setSignedIn] = useState(false);
  const selected = repos.find((item) => item.full_name === repo);

  useEffect(() => {
    let live = true;
    const load = async () => {
      const identity = await fetch(`${API}/auth/me`, { credentials: "include", cache: "no-store" });
      if (!live) return;
      setSignedIn(identity.ok);
      if (!identity.ok) { setRepos([]); setHistory([]); return; }
      const [repoResponse, taskResponse] = await Promise.all([
        fetch(`${API}/github/repositories`, { credentials: "include", cache: "no-store" }),
        fetch(`${API}/agent/tasks?limit=10`, { credentials: "include", cache: "no-store" }),
      ]);
      if (repoResponse.ok) { const data = await repoResponse.json() as { repositories: Repo[] }; if (live) { setRepos(data.repositories); setRepo((current) => current || data.repositories[0]?.full_name || ""); } }
      if (taskResponse.ok && live) setHistory(await taskResponse.json() as Task[]);
    };
    window.addEventListener("astra-auth-change", load); void load().catch(() => { if (live) setError("Planner data could not be loaded."); });
    return () => { live = false; window.removeEventListener("astra-auth-change", load); };
  }, []);

  useEffect(() => {
    if (!selected) { setBranches([]); setBranch(""); return; }
    let live = true; const [owner, name] = selected.full_name.split("/");
    fetch(`${API}/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/branches?account=${encodeURIComponent(selected.account)}`, { credentials: "include", cache: "no-store" })
      .then(async (response) => { if (!response.ok) throw new Error("Repository branches could not be loaded."); return await response.json() as Branch[]; })
      .then((data) => { if (live) { setBranches(data); setBranch(data.find((item) => item.name === selected.default_branch)?.name ?? data[0]?.name ?? ""); } })
      .catch((cause: unknown) => { if (live) setError(cause instanceof Error ? cause.message : "Branches are unavailable."); });
    return () => { live = false; };
  }, [selected]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!selected) return; setBusy(true); setError(""); setTask(null);
    try {
      const csrfResponse = await fetch(`${API}/auth/csrf`, { credentials: "include", cache: "no-store" });
      if (!csrfResponse.ok) throw new Error("Sign in again before creating a plan.");
      const { csrf_token: csrf } = await csrfResponse.json() as { csrf_token: string };
      const response = await fetch(`${API}/agent/tasks`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify({ repository: repo, account: selected.account, branch, task_type: kind, request, share_with_ai: consent }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.detail ?? "Planning request failed.");
      setTask(result as Task); setHistory((items) => [result as Task, ...items.filter((item) => item.id !== result.id)]); setRequest("");
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Planning request failed."); }
    finally { setBusy(false); }
  }

  async function review(decision: "approve" | "reject") {
    if (!task) return; setReviewBusy(true); setError("");
    try {
      const csrfResponse = await fetch(`${API}/auth/csrf`, { credentials: "include", cache: "no-store" });
      if (!csrfResponse.ok) throw new Error("Sign in again before recording a review.");
      const { csrf_token: csrf } = await csrfResponse.json() as { csrf_token: string };
      const response = await fetch(`${API}/agent/tasks/${encodeURIComponent(task.id)}/review`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf }, body: JSON.stringify({ decision }) });
      const result = await response.json(); if (!response.ok) throw new Error(result.detail ?? "Review could not be recorded.");
      const reviewed = result as Task; setTask(reviewed); setHistory((items) => items.map((item) => item.id === reviewed.id ? reviewed : item));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Review could not be recorded."); }
    finally { setReviewBusy(false); }
  }

  return <section className="quiet-panel task-planner" id="ai-engineer"><div className="panel-title"><div><h2>AI task planner</h2><p>Create a bounded plan from a repository and branch.</p></div><span className="live-indicator"><i /> PLANNING ONLY</span></div>
    {!signedIn ? <p className="activity-empty">Sign in and connect GitHub to create a plan.</p> : repos.length === 0 ? <p className="activity-empty">Connect GitHub and authorize a repository to start.</p> : <form className="planner-form" onSubmit={create}>
      <label>Repository<select value={repo} onChange={(e) => setRepo(e.target.value)}>{repos.map((item) => <option key={`${item.account}:${item.full_name}`} value={item.full_name}>{item.full_name}</option>)}</select></label>
      <label>Branch<select value={branch} onChange={(e) => setBranch(e.target.value)}>{branches.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
      <label>Task type<select value={kind} onChange={(e) => setKind(e.target.value)}>{[["explain", "Explain"], ["bug_fix", "Bug fix"], ["feature", "Feature"], ["tests", "Tests"], ["refactor", "Refactor"], ["documentation", "Documentation"], ["ci_investigation", "CI investigation"]].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="planner-request">What should Astra plan?<textarea required minLength={8} maxLength={5000} value={request} onChange={(e) => setRequest(e.target.value)} placeholder="Describe the outcome, constraints, and any relevant context." /></label>
      <label className="planner-consent"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />I authorize Astra to send this request, repository metadata, and up to 8 KB of the selected branch README to the configured AI provider. Astra stores the resulting plan in this workspace.</label>
      {error && <p className="activity-error" role="alert">{error}</p>}<button className="planner-submit" disabled={busy || !branch || !consent}>{busy ? "Planning…" : "Generate reviewable plan"}</button>
    </form>}
    {task && <article className="plan-result"><div className="card-kicker">{task.status.replaceAll("_", " ").toUpperCase()} · {task.model ?? "Planner"}</div><h3>{task.plan?.summary ?? task.failure_reason}</h3>{task.plan && <><h4>Steps</h4><ol>{task.plan.steps.map((step) => <li key={step.id}><strong>{step.title}</strong><p>{step.description}</p><small>Verify: {step.verification}</small></li>)}</ol>{task.plan.risks.length > 0 && <p><strong>Risks:</strong> {task.plan.risks.join(" · ")}</p>}{task.status === "awaiting_review" && <div className="plan-review-actions"><button type="button" disabled={reviewBusy} onClick={() => void review("approve")}>{reviewBusy ? "Saving…" : "Approve plan"}</button><button type="button" disabled={reviewBusy} onClick={() => void review("reject")}>Reject plan</button></div>}</>}{error && <p className="activity-error" role="alert">{error}</p>}<p className="activity-empty">Review records your decision only. No code is changed and isolated execution is not enabled.</p></article>}
    {history.length > 0 && <div className="planner-history"><h3>Recent plans</h3>{history.map((item) => <button key={item.id} type="button" onClick={() => setTask(item)}><span>{item.repository} · {item.branch}</span><small>{item.status.replaceAll("_", " ")} · {item.task_type}</small></button>)}</div>}
  </section>;
}
