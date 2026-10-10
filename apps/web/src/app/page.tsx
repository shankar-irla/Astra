import Link from "next/link";
import { AuthPanel } from "./auth-panel";
import { ThemeToggle } from "./theme-toggle";
import { RepositoryBrowser } from "./repository-browser";
import { ActivityPanel } from "./activity-panel";
import { TaskPlanner } from "./task-planner";

const navigation = ["Overview", "Repositories", "AI Engineer", "Automation", "Pull Requests", "Issues", "Activity", "Integrations", "Settings"];

async function getApiStatus() {
  const apiUrl = process.env.ASTRA_API_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${apiUrl}/ready`, { cache: "no-store", signal: AbortSignal.timeout(2500) });
    if (!response.ok) return { ready: false, message: `API returned ${response.status}` };
    const result: { status: string; database: string } = await response.json();
    return { ready: true, message: `API ${result.status} · database ${result.database}` };
  } catch {
    return { ready: false, message: "API unavailable · start the FastAPI service to connect" };
  }
}

export default async function Home() {
  const api = await getApiStatus();

  return (
    <main className="shell">
      <aside className="sidebar" aria-label="Main navigation">
        <Link href="/" className="brand"><span className="brand-mark">A</span><span>Astra</span></Link>
        <p className="nav-label">WORKSPACE</p>
        <nav>
          {navigation.map((item, index) => (
            index === 0 ? (
              <Link key={item} className="nav-item active" href="/" aria-current="page">
                <span className="nav-glyph" aria-hidden="true">◈</span>{item}<span className="nav-indicator" />
              </Link>
            ) : index === 2 ? (
              <a key={item} className="nav-item" href="#ai-engineer"><span className="nav-glyph" aria-hidden="true">✳</span>{item}</a>
            ) : (
              <a key={item} className={`nav-item${index === 1 ? "" : " future-item"}`} href={index === 1 ? "#repositories" : undefined} aria-disabled={index === 1 ? undefined : "true"} title={index === 1 ? "Browse authorized repositories" : "Available in a later milestone"}>
                <span className="nav-glyph" aria-hidden="true">{["", "▤", "✳", "⌁", "⑂", "◎", "◷", "⌘", "⚙"][index]}</span>{item}
              </a>
            )
          ))}
        </nav>
        <div className="sidebar-bottom"><span className="status-dot" />Local workspace <span className="env-tag">DEV</span></div>
      </aside>

      <section className="main-panel">
        <header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>Overview</strong></div><div className="top-actions"><span className="connection-label"><span className="status-dot" />Account scoped</span><ThemeToggle /><button className="avatar" aria-label="Profile">A</button></div></header>
        <div className="content">
          <div className="eyebrow"><span className="eyebrow-line" />ENGINEERING COMMAND CENTER</div>
          <div className="heading-row"><div><h1>Good work starts<br className="mobile-break" /> with a clear view.</h1><p className="subtitle">Your authorized repositories and engineering workspace in one place.</p></div><span className="date-chip">CONNECTED WORKSPACE · MILESTONE 2</span></div>

          <section className="status-card" aria-label="Service status">
            <div className="status-icon" aria-hidden="true">⌁</div>
            <div className="status-copy"><div className="card-kicker">SYSTEM STATUS</div><h2>{api.ready ? "API and database are ready" : "Waiting for the API"}</h2><p>{api.message}</p></div>
            <span className={`status-pill ${api.ready ? "healthy" : "pending"}`}><span className="status-dot" />{api.ready ? "OPERATIONAL" : "NOT CONNECTED"}</span>
          </section>

          <div className="section-heading"><div><h2>Workspace setup</h2><p>Connect the foundation before starting an engineering run.</p></div><span className="step-count">01 <span>/ 03</span></span></div>
          <div className="setup-grid">
            <article className="setup-card complete"><div className="step-top"><span className="step-icon">✓</span><span className="step-state">READY</span></div><h3>API foundation</h3><p>Health checks, database connection, and the GitHub integration boundary.</p><div className="card-foot"><span>FastAPI</span><span>↗</span></div></article>
            <article className="setup-card complete"><div className="step-top"><span className="step-icon">✓</span><span className="step-state">READY</span></div><h3>Secure identity</h3><p>Argon2 passwords, protected sessions, CSRF checks, and personal workspaces.</p><div className="card-foot"><span>Account boundaries</span><span>✓</span></div></article>
            <article className="setup-card"><div className="step-top"><span className="step-icon">03</span><span className="step-state">CONFIGURE</span></div><h3>Connect GitHub</h3><p>Link your GitHub App to browse authorized branches and files.</p><div className="card-foot muted"><span>GitHub App credentials required</span><span>→</span></div></article>
          </div>

          <div id="repositories"><RepositoryBrowser /></div>

          <div className="lower-grid"><div className="activity-agent-stack"><ActivityPanel /><TaskPlanner /></div><aside className="quiet-panel next-panel"><div className="card-kicker">SECURE WORKSPACE ACCESS</div><h2>Build with control.</h2><p>Create a personal workspace, sign in, and connect a GitHub App.</p><AuthPanel /><div className="guardrail"><span>⌑</span><span>Consequential changes will require approval.</span></div></aside></div>
          <footer>ASTRA <span>·</span> WORK IN PROGRESS <span className="footer-right">Workspace scoped · No sample repository data</span></footer>
        </div>
      </section>
    </main>
  );
}
