import { useState } from "react";
import { api } from "./api";
import { FollowUpCenter } from "./components/FollowUpCenter";
import { NewSource } from "./components/NewSource";
import { ReviewInbox } from "./components/ReviewInbox";
import { usePolling } from "./hooks";

type Tab = "review" | "followups" | "new";

export default function App() {
  const [tab, setTab] = useState<Tab>("review");
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const inbox = usePolling(api.inbox, 3000);
  const waiting = (inbox.data ?? []).reduce((n, r) => n + r.items.filter((i) => i.status === "open").length, 0);

  const sync = async () => {
    setSyncNote("Syncing…");
    try {
      const r = await api.syncNotion();
      setSyncNote(`Synced ${r.tasks} tasks from Notion`);
    } catch (e) {
      setSyncNote(e instanceof Error ? e.message : "Sync failed");
    }
    setTimeout(() => setSyncNote(null), 4000);
  };

  const tabButton = (id: Tab, label: string, badge?: number) => (
    <button className="tab" aria-current={tab === id ? "page" : undefined} onClick={() => setTab(id)}>
      {label}{badge ? <span className="count" aria-label={`${badge} waiting`}>{badge}</span> : null}
    </button>
  );

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12h4l3-8 4 16 3-8h4" /></svg>
          </span>
          <span>ProjectPulse<small>From conversations to verified follow-through</small></span>
        </div>
        <nav className="tabs" aria-label="Sections">
          {tabButton("review", "Review Inbox", waiting)}
          {tabButton("followups", "Follow-Up Center")}
          {tabButton("new", "Add source")}
        </nav>
        <span className="spacer" />
        {syncNote && <span className="muted" style={{ fontSize: 13 }} role="status">{syncNote}</span>}
        <button className="btn small" onClick={sync}>Sync Notion</button>
      </header>
      <main>
        {tab === "review" && <ReviewInbox onAddSource={() => setTab("new")} />}
        {tab === "followups" && <FollowUpCenter onGoToReview={() => setTab("review")} />}
        {tab === "new" && <NewSource onGoToReview={() => setTab("review")} onGoToFollowUps={() => setTab("followups")} />}
      </main>
    </div>
  );
}
