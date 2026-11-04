import { api } from "../api";
import type { Run } from "../types";

const STEPS: { node: string; label: string; audioOnly?: boolean; skippable?: boolean }[] = [
  { node: "ingest", label: "Storing the source" },
  { node: "transcribe", label: "Transcribing the recording", audioOnly: true },
  { node: "retrieve_context", label: "Refreshing tasks from Notion" },
  { node: "extract", label: "Finding commitments and updates" },
  { node: "wait_turn", label: "Waiting for earlier sources to finish" },
  { node: "match", label: "Matching to projects and tasks" },
  { node: "reconcile", label: "Deciding what needs your attention" },
  { node: "human_review", label: "Waiting for your review", skippable: true },
  { node: "validate", label: "Checking Notion is unchanged", skippable: true },
  { node: "execute", label: "Updating Notion", skippable: true },
  { node: "persist", label: "Saving the outcome" },
];

/** Where a run is, as a checklist. Steps before the current node are done; a terminal status settles the rest. */
export function RunProgress({ run, onRetry }: { run: Run; onRetry?: () => void }) {
  const steps = STEPS.filter((s) => !s.audioOnly || run.kind === "audio");
  const current = steps.findIndex((s) => s.node === run.current_node);
  const finished = run.status === "completed";

  const stateOf = (i: number): "done" | "active" | "failed" | "todo" | "skipped" => {
    if (finished) return run.skipped_review && steps[i].skippable ? "skipped" : "done";
    if (current < 0) return "todo";
    if (i < current) return "done";
    if (i === current) return run.status === "failed" ? "failed" : run.status === "waiting_for_review" ? "active" : "active";
    return "todo";
  };

  const retry = async () => {
    await api.retry(run.thread_id);
    onRetry?.();
  };

  return (
    <div className="card progress" aria-live="polite">
      <div className="row">
        {run.status === "running" && <span className="spinner" />}
        <strong>{run.message || "Working…"}</strong>
      </div>
      {run.status === "failed" && (
        <div className="stack" style={{ marginTop: 10 }}>
          <div className="error">{run.message}</div>
          <button className="btn small" onClick={retry}>Retry from where it stopped</button>
        </div>
      )}
      <ol className="steps">
        {steps.map((s, i) => {
          const st = stateOf(i);
          return (
            <li key={s.node} className={`step ${st}`}>
              <span className="dot">{st === "done" ? "✓" : st === "failed" ? "!" : st === "skipped" ? "–" : ""}</span>
              {st === "active" && run.status === "running" && <span className="spinner" />}
              {s.label}{st === "skipped" && <span className="faint"> (not needed)</span>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
