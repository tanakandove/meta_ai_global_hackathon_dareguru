import { useState } from "react";
import { api } from "../api";
import { deadlineWords, initials } from "../format";
import { usePolling } from "../hooks";
import type { FollowUp } from "../types";
import { CommitmentPanel } from "./CommitmentPanel";

type Filter = "outstanding" | "overdue" | "upcoming" | "blocked" | "requests" | "resolved";

const FILTERS: { id: Filter; label: string; tone: string; test: (c: FollowUp) => boolean }[] = [
  { id: "outstanding", label: "Outstanding", tone: "", test: (c) => c.state === "open" || c.state === "resolution_proposed" },
  { id: "overdue", label: "Overdue", tone: "red", test: (c) => c.is_overdue && c.state !== "proposed" },
  { id: "upcoming", label: "Due this week", tone: "amber", test: (c) => c.is_upcoming && c.state !== "proposed" },
  { id: "blocked", label: "Blocked", tone: "red", test: (c) => c.is_blocked },
  { id: "requests", label: "Requests for action", tone: "", test: (c) => c.is_request && c.state === "open" },
  { id: "resolved", label: "Recently resolved", tone: "green", test: (c) => c.state === "resolved" },
];

const TONE = { overdue: "red", soon: "amber", calm: "", none: "" } as const;

export function FollowUpCenter({ onGoToReview }: { onGoToReview: () => void }) {
  const { data, error } = usePolling(api.followups, 3000);
  const [filter, setFilter] = useState<Filter>("outstanding");
  const [selected, setSelected] = useState<number | null>(null);

  const all = data?.commitments ?? [];
  const today = data?.as_of ?? new Date().toLocaleDateString("en-CA");
  const shown = all.filter(FILTERS.find((f) => f.id === filter)!.test);
  const waiting = all.filter((c) => c.needs_approval).length;

  return (
    <div>
      {error && <div className="error" style={{ marginTop: 16 }}>{error}</div>}
      {waiting > 0 && (
        <div className="card progress row" style={{ marginTop: 16 }}>
          <div style={{ flex: 1 }}><strong>{waiting} item(s) are waiting for your approval.</strong> <span className="muted">They are not counted as confirmed work until you decide.</span></div>
          <button className="btn small" onClick={onGoToReview}>Review them</button>
        </div>
      )}
      <div className="tiles" role="tablist" aria-label="Filter follow-ups">
        {FILTERS.map((f) => {
          const n = all.filter(f.test).length;
          return (
            <button key={f.id} role="tab" aria-selected={filter === f.id} className={`card tile ${f.tone} ${filter === f.id ? "active" : ""}`}
              onClick={() => { setFilter(f.id); setSelected(null); }}>
              <div className="n">{n}</div><div className="l">{f.label}</div>
            </button>
          );
        })}
      </div>

      <div className={`layout ${selected !== null ? "with-panel" : ""}`}>
        <div className="card list">
          {shown.length === 0 && <div className="empty"><h3>Nothing here</h3><p>{all.length === 0 ? "Add a meeting or email and approve what it finds." : "No follow-ups match this filter."}</p></div>}
          {shown.map((c) => {
            const dl = deadlineWords(c.deadline, today);
            return (
              <button key={c.id} className="fu-row" aria-current={selected === c.id} onClick={() => setSelected(c.id)}>
                <span className={`avatar ${c.owner ? "" : "unknown"}`} title={c.owner ?? "Owner not confirmed"}>{initials(c.owner)}</span>
                <span>
                  <div className="fu-title">{c.title}</div>
                  <div className="fu-sub"><span>{c.owner ?? "No owner yet"}</span>{c.project && <><span>·</span><span>{c.project}</span></>}</div>
                </span>
                <span className="fu-right">
                  {c.is_blocked && <span className="badge red">Blocked</span>}
                  {c.is_request && <span className="badge violet">Request</span>}
                  {c.state === "resolution_proposed" && <span className="badge amber">Completion to confirm</span>}
                  {c.state === "resolved" ? <span className="badge green">Resolved</span> : <span className={`badge ${TONE[dl.tone]}`}>{dl.text}</span>}
                </span>
              </button>
            );
          })}
        </div>
        {selected !== null && <CommitmentPanel key={selected} id={selected} today={today} onClose={() => setSelected(null)} onGoToReview={onGoToReview} />}
      </div>
    </div>
  );
}
