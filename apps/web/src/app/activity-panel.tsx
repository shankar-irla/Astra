"use client";

import { useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_ASTRA_API_URL ?? "http://localhost:8000";
type Activity = {
  id: string;
  operation: string;
  repository: string;
  github_login: string;
  status: "pending" | "succeeded" | "failed";
  resource_number: number | null;
  url: string | null;
  created_at: string;
};

export function ActivityPanel() {
  const [items, setItems] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);
  const [signedIn, setSignedIn] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true); setError("");
      try {
        const identity = await fetch(`${API_URL}/auth/me`, { credentials: "include", cache: "no-store" });
        if (!identity.ok) {
          if (active) { setSignedIn(false); setItems([]); setLoading(false); }
          return;
        }
        const response = await fetch(`${API_URL}/github/activity?limit=25`, { credentials: "include", cache: "no-store" });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail ?? "Activity could not be loaded.");
        if (active) { setSignedIn(true); setItems(result as Activity[]); }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "Activity is unavailable.");
      } finally {
        if (active) setLoading(false);
      }
    };
    window.addEventListener("astra-auth-change", load);
    window.addEventListener("astra-activity-change", load);
    void load();
    return () => {
      active = false;
      window.removeEventListener("astra-auth-change", load);
      window.removeEventListener("astra-activity-change", load);
    };
  }, []);

  return (
    <section className="quiet-panel" aria-label="Recent engineering activity">
      <div className="panel-title">
        <div><h2>GitHub activity</h2><p>Workspace-scoped writes performed through Astra.</p></div>
        <span className="live-indicator"><i /> AUDIT TRAIL</span>
      </div>
      {loading ? <p className="activity-empty" role="status">Loading activity…</p>
        : error ? <p className="activity-error" role="alert">{error}</p>
          : !signedIn ? <p className="activity-empty">Sign in to view workspace activity.</p>
            : items.length ? <div className="activity-list">{items.map((item) => (
              <article className="activity-item" key={item.id}>
                <span className={`activity-state ${item.status}`} aria-label={item.status} />
                <div className="activity-copy">
                  <strong>{item.operation === "create_issue" ? "Created an issue" : item.operation}</strong>
                  <span>{item.repository}{item.resource_number ? ` · #${item.resource_number}` : ""}</span>
                  <small>{item.github_login} · {new Date(item.created_at).toLocaleString()}</small>
                </div>
                {item.url && <a href={item.url} target="_blank" rel="noreferrer">Open ↗</a>}
              </article>
            ))}</div>
              : <p className="activity-empty">No GitHub mutations have been recorded in this workspace.</p>}
    </section>
  );
}
