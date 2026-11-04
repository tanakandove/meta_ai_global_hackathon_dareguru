import { useState, type FormEvent } from "react";
import { api } from "../api";
import { usePolling } from "../hooks";
import { SAMPLE_FOLLOW_UP_EMAIL, SAMPLE_TRANSCRIPT } from "../samples";
import { RunProgress } from "./RunProgress";

type Kind = "audio" | "transcript" | "email" | "text";

const KINDS: { id: Kind; label: string; hint: string }[] = [
  { id: "audio", label: "Meeting recording", hint: "A WAV file: mono, 16-bit, 16 or 24 kHz, up to 10 minutes." },
  { id: "transcript", label: "Meeting transcript", hint: "Paste a transcript. Lines like “John: …” keep who said what." },
  { id: "email", label: "Email", hint: "Paste the email with its From, Subject and Attachments lines." },
  { id: "text", label: "Text update", hint: "Paste a message from chat or anywhere else." },
];

const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD in the viewer's timezone

export function NewSource({ onGoToReview, onGoToFollowUps }: { onGoToReview: () => void; onGoToFollowUps: () => void }) {
  const [kind, setKind] = useState<Kind>("audio");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [date, setDate] = useState(today());
  const [time, setTime] = useState("09:00");
  const [tz, setTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);

  const run = usePolling(() => api.run(threadId!), 1000, threadId !== null);
  const status = run.data?.status;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const form = new FormData();
      form.set("kind", kind);
      form.set("meeting_date", date);
      form.set("meeting_time", time);
      form.set("tz", tz);
      if (kind === "audio" && file) form.set("file", file);
      else form.set("text", text);
      setThreadId((await api.createRun(form)).thread_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const reset = () => { setThreadId(null); setFile(null); setText(""); setError(null); };
  const active = KINDS.find((k) => k.id === kind)!;
  const ready = kind === "audio" ? file !== null : text.trim().length > 0;

  if (threadId) {
    return (
      <div className="narrow stack">
        {run.data ? <RunProgress run={run.data} onRetry={run.reload} /> : <div className="card progress"><span className="spinner" /> Starting…</div>}
        {status === "waiting_for_review" && (
          <div className="card progress row">
            <div style={{ flex: 1 }}><strong>{run.data?.items.length} item(s) are ready for your review.</strong><div className="muted">Nothing has been written to Notion yet.</div></div>
            <button className="btn primary" onClick={onGoToReview}>Open Review Inbox</button>
          </div>
        )}
        {status === "completed" && (
          <div className="card progress row">
            <div style={{ flex: 1 }}><strong>Done.</strong><div className="muted">{run.data?.message}</div></div>
            <button className="btn primary" onClick={onGoToFollowUps}>Open Follow-Up Center</button>
          </div>
        )}
        {status === "duplicate" && <div className="card progress"><strong>You have already added this.</strong><div className="muted">The same source was processed before, so nothing was repeated.</div></div>}
        {status === "no_events" && <div className="card progress"><strong>Nothing to follow up.</strong><div className="muted">No commitments, requests or updates were found in this source.</div></div>}
        {run.error && <div className="error">{run.error}</div>}
        <button className="btn" onClick={reset}>Add another source</button>
      </div>
    );
  }

  return (
    <form className="narrow card stack" style={{ padding: 22 }} onSubmit={submit}>
      <div>
        <h2>Add a source</h2>
        <p className="muted" style={{ margin: "4px 0 0" }}>ProjectPulse finds commitments and updates, matches them to your Notion tasks, and asks you before changing anything.</p>
      </div>
      <div className="seg" role="group" aria-label="Source type">
        {KINDS.map((k) => (
          <button type="button" key={k.id} aria-pressed={kind === k.id} onClick={() => { setKind(k.id); setError(null); }}>{k.label}</button>
        ))}
      </div>
      <p className="faint" style={{ margin: 0, fontSize: 13 }}>{active.hint}</p>

      {kind === "audio" ? (
        <label className="drop" style={{ cursor: "pointer", fontWeight: 400 }}>
          <input type="file" accept=".wav,audio/wav" style={{ display: "none" }} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {file ? <strong>{file.name}</strong> : <><strong>Choose a WAV recording</strong><div>or drop one here</div></>}
        </label>
      ) : (
        <div>
          <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste here…" aria-label="Source text" />
          <div className="row" style={{ marginTop: 6 }}>
            <span className="faint" style={{ fontSize: 13 }}>Try an example:</span>
            <button type="button" className="btn small ghost" onClick={() => { setKind("transcript"); setText(SAMPLE_TRANSCRIPT); }}>Meeting transcript</button>
            <button type="button" className="btn small ghost" onClick={() => { setKind("email"); setText(SAMPLE_FOLLOW_UP_EMAIL); }}>Follow-up email</button>
          </div>
        </div>
      )}

      <div className="grid3">
        <div><label htmlFor="d">When did it happen?</label><input id="d" type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></div>
        <div><label htmlFor="t">Time</label><input id="t" type="time" value={time} onChange={(e) => setTime(e.target.value)} required /></div>
        <div><label htmlFor="z">Timezone</label><input id="z" value={tz} onChange={(e) => setTz(e.target.value)} required /></div>
      </div>
      <p className="faint" style={{ margin: 0, fontSize: 13 }}>Needed to turn “by Friday” or “yesterday” into real dates.</p>

      {error && <div className="error" role="alert">{error}</div>}
      <div><button className="btn primary" disabled={!ready || busy}>{busy ? "Starting…" : "Analyze"}</button></div>
    </form>
  );
}
