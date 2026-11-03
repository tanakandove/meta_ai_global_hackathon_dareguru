import { useState } from "react";
import { api } from "../api";
import { longDateTime, sourceLabel } from "../format";
import { usePolling } from "../hooks";
import type { DecisionAction, Edits, Project, RunDetail } from "../types";
import { ReviewCard } from "./ReviewCard";
import { RunProgress } from "./RunProgress";

function RunReview({ run, people, projects, reload }: { run: RunDetail; people: string[]; projects: Project[]; reload: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [confirmRerun, setConfirmRerun] = useState(false);
  const [applying, setApplying] = useState(false);
  const open = run.items.filter((i) => i.status === "open");
  const undecided = run.undecided_item_ids?.length ?? 0;
  const s = run.source;

  const decide = async (id: number, action: DecisionAction, edits?: Edits) => {
    await api.decide(id, action, edits);
    reload();
  };
  const rerun = async () => {
    setError(null);
    try { await api.rerun(run.thread_id); setConfirmRerun(false); reload(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setConfirmRerun(false); }
  };
  const apply = async () => {
    setApplying(true); setError(null);
    try { await api.apply(run.thread_id); reload(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setApplying(false); }
  };

  return (
    <section className="card run-card" aria-label={`Review for ${s ? sourceLabel(s.kind) : run.thread_id}`}>
      <div className="run-head">
        <h2>{s ? (s.filename ?? sourceLabel(s.kind)) : "Source"}</h2>
        <div className="muted" style={{ fontSize: 14, marginTop: 2 }}>
          {s && <>{sourceLabel(s.kind)} · {longDateTime(s.occurred_at)}{s.meeting_project && s.meeting_project !== "unknown" ? ` · ${s.meeting_project}` : ""}</>}
        </div>
        <div className="row wrap" style={{ marginTop: 10 }}>
          {!confirmRerun ? (
            <button className="btn small ghost" onClick={() => setConfirmRerun(true)}
              title="Useful after you add a project or task in Notion">Re-run with current Notion data</button>
          ) : (
            <>
              <span className="muted" style={{ fontSize: 13 }}>This discards the decisions you have not applied and analyzes the source again.</span>
              <button className="btn small primary" onClick={rerun}>Re-run</button>
              <button className="btn small ghost" onClick={() => setConfirmRerun(false)}>Cancel</button>
            </>
          )}
        </div>
        {s && s.speakers.some((sp) => sp.name) && (
          <div className="chips" aria-label="Speakers identified from the conversation">
            {s.speakers.filter((sp) => sp.name).map((sp) => (
              <span key={sp.label} className="badge" title={sp.basis}>{sp.label === sp.name ? sp.name : `Speaker ${sp.label} = ${sp.name}`}</span>
            ))}
          </div>
        )}
      </div>
      <div className="items">
        {open.map((item) => <ReviewCard key={item.id} item={item} people={people} projects={projects} readOnly={false} onDecide={decide} />)}
      </div>
      <div className="applybar">
        <div style={{ flex: 1 }}>
          {undecided === 0
            ? <strong>All {open.length} decided. Nothing is written to Notion until you apply.</strong>
            : <><strong>{open.length - undecided} of {open.length} decided.</strong> <span className="muted">{undecided} left before you can apply.</span></>}
          {error && <div className="error" role="alert" style={{ marginTop: 8 }}>{error}</div>}
        </div>
        <button className="btn primary" disabled={undecided > 0 || applying} onClick={apply}>Apply decisions</button>
      </div>
    </section>
  );
}

export function ReviewInbox({ onAddSource }: { onAddSource: () => void }) {
  const inbox = usePolling(api.inbox, 2000);
  const runs = usePolling(api.runs, 2000);
  const people = usePolling(api.people, 30000);
  const projects = usePolling(api.projects, 30000);
  const working = (runs.data ?? []).filter((r) => r.status === "running" || r.status === "failed");

  return (
    <div>
      {inbox.error && <div className="error" style={{ marginTop: 16 }}>{inbox.error}</div>}
      {working.map((r) => <div key={r.thread_id} style={{ marginTop: 16 }}><RunProgress run={r} onRetry={runs.reload} /></div>)}
      {(inbox.data ?? []).map((run) => (
        <RunReview key={run.thread_id} run={run} people={people.data ?? []} projects={projects.data ?? []} reload={() => { inbox.reload(); runs.reload(); }} />
      ))}
      {inbox.data && inbox.data.length === 0 && working.length === 0 && (
        <div className="empty">
          <h3>Nothing needs your review</h3>
          <p>When a meeting or email contains something worth following up, it will show up here for you to approve.</p>
          <button className="btn primary" onClick={onAddSource}>Add a source</button>
        </div>
      )}
    </div>
  );
}
