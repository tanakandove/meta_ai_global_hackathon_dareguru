import { api } from "../api";
import { deadlineWords, longDateTime, shortDate, sourceLabel } from "../format";
import { usePolling } from "../hooks";
import type { CommitmentDetail } from "../types";

const STATE: Record<CommitmentDetail["state"], { label: string; tone: string }> = {
  proposed: { label: "Waiting for approval", tone: "amber" },
  open: { label: "Open", tone: "accent" },
  resolution_proposed: { label: "Completion awaiting approval", tone: "amber" },
  resolved: { label: "Resolved", tone: "green" },
  rejected: { label: "Rejected", tone: "" },
};

const RELATION: Record<string, string> = {
  origin: "Committed", completion: "Completion reported", progress: "Progress", blocker: "Blocker", contradiction: "Contradiction",
};

const STEP_LABEL: Record<string, string> = {
  proposed: "Proposed", open: "Confirmed open", resolution_proposed: "Completion proposed", resolved: "Resolved", rejected: "Rejected",
};

function attention(d: CommitmentDetail, today: string): string | null {
  const dl = deadlineWords(d.deadline, today);
  const blocker = d.evidence.find((e) => e.relation === "blocker");
  if (d.state === "resolution_proposed") return "Someone says this is finished. It stays open until you confirm in the Review Inbox.";
  if (d.state === "proposed") return "This commitment has not been confirmed yet. Review it in the Review Inbox.";
  if (d.state === "open" && blocker) return `Blocked: “${blocker.excerpt}” (${blocker.speaker}).`;
  if (d.state === "open" && dl.tone === "overdue") return `${dl.text}. No completion evidence has been recorded.`;
  if (d.state === "open" && dl.tone === "soon") return `${dl.text}.`;
  return null;
}

export function CommitmentPanel({ id, today, onClose, onGoToReview }: { id: number; today: string; onClose: () => void; onGoToReview: () => void }) {
  const { data: d, error } = usePolling(() => api.commitment(id), 3000);
  if (error && !d) return <aside className="card panel"><div className="error">{error}</div></aside>;
  if (!d) return <aside className="card panel"><span className="spinner" /></aside>;

  const st = STATE[d.state];
  const why = attention(d, today);
  const dl = deadlineWords(d.deadline, today);

  return (
    <aside className="card panel" aria-label="Commitment details">
      <div className="row" style={{ alignItems: "flex-start" }}>
        <div style={{ flex: 1 }}>
          <span className={`badge ${st.tone}`}>{st.label}</span>
          <h2 style={{ marginTop: 8 }}>{d.task?.title ?? d.deliverable}</h2>
        </div>
        <button className="btn small ghost" onClick={onClose} aria-label="Close details">✕</button>
      </div>

      {why && <div className="sec"><div className="error" style={{ background: "var(--amber-soft)", color: "var(--amber)" }}><strong>Why it needs attention: </strong>{why}</div></div>}

      {!d.original && (
        <div className="sec">
          <h3>Original commitment</h3>
          <p className="muted" style={{ margin: 0 }}>This task was already in Notion before ProjectPulse. It is being tracked because of the evidence below.</p>
        </div>
      )}
      {d.original && (
        <div className="sec">
          <h3>Original {d.original.type === "request" ? "request" : "commitment"}</h3>
          <blockquote className="quote" style={{ margin: 0 }}>“{d.original.excerpt}”<cite>{d.original.speaker} · {sourceLabel(d.original.source_kind)}{d.original.filename ? ` (${d.original.filename})` : ""} · {longDateTime(d.original.occurred_at)}</cite></blockquote>
        </div>
      )}

      <div className="sec">
        <h3>Details</h3>
        <dl className="kv" style={{ margin: 0 }}>
          <dt>Project</dt><dd>{d.project ?? "—"}</dd>
          <dt>Responsible</dt><dd>{d.owner ?? <em className="muted">Not confirmed</em>}</dd>
          <dt>Deadline</dt><dd>{d.deadline ? `${shortDate(d.deadline)} · ${dl.text}` : "None"}{d.deadline && d.deadline_basis === "inferred" ? " (inferred)" : ""}</dd>
          <dt>Status</dt><dd>{st.label}</dd>
          <dt>Notion task</dt>
          <dd>{d.task ? <>{d.task.title}<div className="muted" style={{ fontSize: 13 }}>Status in Notion: {d.task.status}</div></> : <em className="muted">Not linked yet</em>}</dd>
        </dl>
      </div>

      <div className="sec">
        <h3>Supporting evidence</h3>
        {d.evidence.length === 0 ? <p className="muted" style={{ margin: 0 }}>No evidence recorded yet.</p> : (
          <ul className="timeline">
            {d.evidence.map((e, i) => (
              <li key={i} className={e.relation === "blocker" ? "blocker" : e.strength}>
                <div className="row wrap">
                  <strong>{RELATION[e.relation] ?? e.relation}</strong>
                  {e.relation === "completion" && <span className={`badge ${e.strength === "verified" ? "green" : "blue"}`}>{e.strength === "verified" ? "Verified" : "Claimed"}</span>}
                </div>
                <div style={{ fontStyle: "italic" }}>“{e.excerpt}”</div>
                <div className="faint" style={{ fontSize: 12 }}>{e.speaker} · {sourceLabel(e.source_kind)} · {longDateTime(e.occurred_at)}</div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {d.history.length > 0 && (
        <div className="sec">
          <h3>History</h3>
          <ul className="timeline">
            {d.history.map((h, i) => <li key={i}><strong>{STEP_LABEL[h.to] ?? h.to}</strong> <span className="faint">· {longDateTime(h.at)}</span></li>)}
          </ul>
        </div>
      )}

      <div className="sec row wrap">
        {d.actions.map((a) => a.url
          ? <a key={a.id} className="btn" href={a.url} target="_blank" rel="noreferrer">{a.label} ↗</a>
          : <button key={a.id} className="btn" onClick={onGoToReview}>{a.label}</button>)}
      </div>
    </aside>
  );
}
