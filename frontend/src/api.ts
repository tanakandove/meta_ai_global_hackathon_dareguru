import type { CommitmentDetail, DecisionAction, Edits, FollowUp, Project, Run, RunDetail } from "./types";

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError("Cannot reach the ProjectPulse server. Is it running?");
  }
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      // FastAPI returns a string for our own errors and a list for validation errors.
      message = Array.isArray(body.detail) ? body.detail.map((d: { msg: string }) => d.msg).join("; ") : body.detail ?? message;
    } catch { /* keep statusText */ }
    throw new ApiError(message);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export const api = {
  inbox: () => request<RunDetail[]>("/api/inbox"),
  runs: () => request<Run[]>("/api/runs"),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  createRun: (form: FormData) => request<{ thread_id: string }>("/api/runs", { method: "POST", body: form }),
  decide: (itemId: number, action: DecisionAction, edits: Edits = {}) =>
    request<{ ok: boolean }>(`/api/review/${itemId}/decision`, json({ action, edits })),
  apply: (id: string) => request<{ thread_id: string }>(`/api/runs/${id}/apply`, { method: "POST" }),
  rerun: (id: string) => request<{ thread_id: string }>(`/api/runs/${id}/rerun`, { method: "POST" }),
  projects: () => request<Project[]>("/api/projects"),
  retry: (id: string) => request<{ thread_id: string }>(`/api/runs/${id}/retry`, { method: "POST" }),
  followups: () => request<{ as_of: string; commitments: FollowUp[] }>("/api/followups"),
  commitment: (id: number) => request<CommitmentDetail>(`/api/commitments/${id}`),
  people: () => request<string[]>("/api/people"),
  syncNotion: () => request<{ tasks: number; projects: number; people: number }>("/api/notion/sync", { method: "POST" }),
};
