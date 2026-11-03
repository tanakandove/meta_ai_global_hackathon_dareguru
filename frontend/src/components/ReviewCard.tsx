import { useState } from "react";
import { clock, shortDate } from "../format";
import type { DecisionAction, Edits, ReviewItem } from "../types";

const KIND: Record<ReviewItem["kind"], { label: string; tone: string }> = {
  task_match: { label: "Task match", tone: "accent" },
  ambiguous_owner_or_deadline: { label: "Needs owner or date", tone: "amber" },
  uncertain_extraction: { label: "Unclear", tone: "amber" },
  possible_duplicate: { label: "Possible duplicate", tone: "violet" },
  conflict: { label: "Conflicting information", tone: "red" },
  potential_completion: { label: "Possible completion", tone: "green" },
  suggestion: { label: "Suggestion", tone: "blue" },
};

function headline(item: ReviewItem): string {
  switch (item.action.type) {
    case "link_task": return item.kind === "ambiguous_owner_or_deadline" ? "Confirm the details and link to an existing task" : "Link this commitment to an existing task";
    case "create_task": return "Create a new task for this commitment";
    case "resolve_task": return item.action.strength === "verified" ? "Mark this task done (evidence attached)" : "Mark this task done?";
    default:
      return item.kind === "suggestion" ? "A suggestion, not a commitment"
        : item.kind === "conflict" ? "Something does not add up"
        : item.kind === "possible_duplicate" ? "Already being tracked"
        : "ProjectPulse could not tell";
  }
}

export function ReviewCard({ item, people, readOnly, onDecide }: {
  item: ReviewItem; people: string[]; readOnly: boolean;
  onDecide: (id: number, action: DecisionAction, edits?: Edits) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [owner, setOwner] = useState(item.decision?.edits.owner ?? item.commitment?.owner ?? item.action.suggested_owner ?? "");
  const [due, setDue] = useState(item.decision?.edits.due ?? item.event?.deadline_date ?? "");
  const [title, setTitle] = useState(item.decision?.edits.title ?? item.action.task?.title ?? "");
  const [page, setPage] = useState(item.decision?.edits.notion_page_id ?? item.task?.page_id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effect = item.action.type !== "none";
  const decided = item.decision?.action;
  const locked = readOnly || item.locked || item.status === "resolved";
  const kind = KIND[item.kind];
  const e = item.event;
  const at = clock(e?.start_ms ?? null);

  const decide = async (action: DecisionAction, edits: Edits = {}) => {
    setBusy(true); setError(null);
    try { await onDecide(item.id, action, edits); setEditing(false); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };

  const saveEdit = () => {
    const edits: Edits = {};
    if (item.action.type === "create_task") {
      if (title && title !== item.action.task?.title) edits.title = title;
    }
    if (owner && owner !== (item.action.task?.owner ?? item.commitment?.owner ?? "")) edits.owner = owner;
    if (due && due !== (item.event?.deadline_date ?? "")) edits.due = due;
    if (item.action.type === "link_task" && page && page !== item.task?.page_id) edits.notion_page_id = page;
    if (Object.keys(edits).length === 0) { setError("Change something first, or choose Approve."); return; }
    decide("edit", edits);
  };

  const assignSuggested = () => decide("edit", { owner: item.action.suggested_owner! });

  return (
    <article className={`item ${decided ? "decided" : ""}`} aria-label={headline(item)}>
      <div className="row wrap">
        <span className={`badge ${kind.tone}`}>{kind.label}</span>
        {item.action.type === "resolve_task" && (
          <span className={`badge ${item.action.strength === "verified" ? "green" : "blue"}`}>
            {item.action.strength === "verified" ? "Verified: attachment" : "Claimed, not verified"}
          </span>
        )}
        {e && e.deadline_basis === "inferred" && <span className="badge amber">Date inferred</span>}
        {e && !e.verified_quote && <span className="badge red">Quote not found in source</span>}
      </div>
      <h3 style={{ marginTop: 8 }}>{headline(item)}</h3>

      {e && (
        <blockquote className="quote">
          “{e.excerpt}”
          <cite>{e.speaker}{at ? ` · ${at}` : ""}</cite>
        </blockquote>
      )}
      <div className="why"><strong>Why you are being asked: </strong>{item.reason}</div>

      {effect && (
        <div className="change" aria-label="What will change">
          {item.action.type === "link_task" && (<>
            <div><span className="k">Commitment</span><span>{item.commitment?.deliverable}</span></div>
            <div><span className="k">Linked to</span><span><strong>{item.task?.title}</strong>{item.task?.owner ? ` · ${item.task.owner}` : ""}{item.task?.due ? ` · due ${shortDate(item.task.due)}` : ""}</span></div>
            <div><span className="k">Owner</span><span>{item.commitment?.owner ?? <em className="muted">Unknown</em>}</span></div>
            <div><span className="k">Notion</span><span className="muted">Not changed. Only ProjectPulse starts tracking it.</span></div>
          </>)}
          {item.action.type === "create_task" && (<>
            <div><span className="k">New task</span><span><strong>{item.action.task?.title}</strong></span></div>
            <div><span className="k">Owner</span><span>{item.action.task?.owner ?? <em className="muted">Unknown</em>}</span></div>
            <div><span className="k">Due</span><span>{shortDate(item.action.task?.due || null)}</span></div>
            <div><span className="k">Notion</span><span className="muted">A new task is created in your Tasks database.</span></div>
          </>)}
          {item.action.type === "resolve_task" && (<>
            <div><span className="k">Task</span><span><strong>{item.task?.title ?? item.commitment?.deliverable}</strong></span></div>
            <div><span className="k">Status</span><span>{item.task?.status ?? "—"} → <strong>Done</strong></span></div>
            <div><span className="k">Notion</span><span className="muted">Status set to Done and the evidence is added to the task.</span></div>
          </>)}
        </div>
      )}

      {!locked && item.action.suggested_owner && !item.commitment?.owner && decided !== "edit" && (
        <div style={{ marginTop: 10 }}>
          <button className="btn small" disabled={busy} onClick={assignSuggested}>Assign to {item.action.suggested_owner} (task owner) and approve</button>
        </div>
      )}

      <div className="decide" role="group" aria-label="Your decision">
        <button className="pick approve" aria-pressed={decided === "approve"} disabled={locked || busy} onClick={() => decide("approve")}>
          {effect ? "Approve" : "Acknowledge"}
        </button>
        {effect && (
          <button className="pick edit" aria-pressed={decided === "edit" || editing} disabled={locked || busy} onClick={() => setEditing((v) => !v)}>Edit…</button>
        )}
        <button className="pick reject" aria-pressed={decided === "reject"} disabled={locked || busy} onClick={() => decide("reject")}>
          {effect ? "Reject" : "Dismiss"}
        </button>
        {decided === "edit" && !editing && <span className="muted" style={{ fontSize: 13 }}>Edited: {Object.entries(item.decision!.edits).map(([k, v]) => `${k} → ${k === "due" ? shortDate(v) : v}`).join(", ")}</span>}
      </div>

      {editing && !locked && (
        <div className="editor">
          {item.action.type === "create_task" && (
            <div><label htmlFor={`t${item.id}`}>Task title</label><input id={`t${item.id}`} value={title} onChange={(ev) => setTitle(ev.target.value)} /></div>
          )}
          {item.action.type === "link_task" && item.alternatives.length > 0 && (
            <div>
              <label htmlFor={`p${item.id}`}>Link to task</label>
              <select id={`p${item.id}`} value={page} onChange={(ev) => setPage(ev.target.value)}>
                {item.alternatives.map((a) => <option key={a.page_id} value={a.page_id}>{a.title}{a.owner ? ` (${a.owner})` : ""}</option>)}
              </select>
            </div>
          )}
          <div className="row wrap">
            <div style={{ flex: 1, minWidth: 160 }}>
              <label htmlFor={`o${item.id}`}>Owner</label>
              <select id={`o${item.id}`} value={owner} onChange={(ev) => setOwner(ev.target.value)}>
                <option value="">Unknown</option>
                {[...new Set([...people, ...(owner ? [owner] : [])])].map((p) => <option key={p}>{p}</option>)}
              </select>
            </div>
            <div style={{ flex: 1, minWidth: 160 }}>
              <label htmlFor={`d${item.id}`}>Due date</label>
              <input id={`d${item.id}`} type="date" value={due} onChange={(ev) => setDue(ev.target.value)} />
            </div>
          </div>
          <div className="row"><button className="btn primary small" disabled={busy} onClick={saveEdit}>Save and approve with changes</button><button className="btn small ghost" onClick={() => setEditing(false)}>Cancel</button></div>
        </div>
      )}
      {error && <div className="error" role="alert" style={{ marginTop: 10 }}>{error}</div>}
    </article>
  );
}
