export type RunStatus = "running" | "waiting_for_review" | "completed" | "duplicate" | "no_events" | "failed" | "superseded";

export interface Project { id: number; name: string }

export interface Run {
  thread_id: string;
  source_id: number | null;
  kind: string;
  status: RunStatus;
  current_node: string | null;
  message: string;
  created_at: string;
  updated_at: string;
  undecided_item_ids?: number[];
  can_apply?: boolean;
  skipped_review?: boolean;
}

export interface Speaker { label: string; name: string; confidence: string; basis: string }

export interface SourceSummary {
  id: number; kind: string; occurred_at: string; timezone: string;
  filename: string | null; meeting_project: string | null; speakers: Speaker[];
}

export type Edits = { owner?: string; due?: string; title?: string; notion_page_id?: string; project_id?: number };
export type DecisionAction = "approve" | "reject" | "edit";

export interface TaskRef { page_id: string; title: string; owner: string | null; due: string | null; status: string; url: string | null }

export interface ReviewItem {
  id: number;
  kind: "uncertain_extraction" | "ambiguous_owner_or_deadline" | "task_match" | "possible_duplicate" | "conflict" | "potential_completion" | "suggestion";
  reason: string;
  status: "open" | "resolved";
  action: {
    type: "link_task" | "create_task" | "resolve_task" | "none" | "retry_of";
    commitment_id?: number; notion_page_id?: string | null; suggested_owner?: string | null;
    strength?: "claimed" | "verified"; task?: { title: string; owner: string | null; due: string; project_id: number | null };
  };
  decision: { action: DecisionAction; edits: Edits } | null;
  locked: boolean;
  event: {
    type: string; excerpt: string; speaker: string; owner: string | null; deliverable: string;
    deadline_text: string; deadline_date: string | null; deadline_basis: string; start_ms: number | null; verified_quote: boolean;
  } | null;
  commitment: { id: number; state: string; owner: string | null; deliverable: string } | null;
  task: TaskRef | null;
  alternatives: { page_id: string; title: string; owner: string | null; due: string | null }[];
}

export interface RunDetail extends Run { source: SourceSummary | null; items: ReviewItem[] }

export interface FollowUp {
  id: number; state: "proposed" | "open" | "resolution_proposed" | "resolved" | "rejected";
  title: string; deliverable: string; owner: string | null; project: string | null;
  deadline: string | null; deadline_basis: string; task_title: string | null; task_status: string | null;
  notion_url: string | null; evidence_count: number;
  is_blocked: boolean; is_request: boolean; is_overdue: boolean; is_upcoming: boolean; needs_approval: boolean;
  updated_at: string;
}

export interface CommitmentDetail {
  id: number; state: FollowUp["state"]; deliverable: string; owner: string | null; project: string | null;
  deadline: string | null; deadline_basis: string;
  original: { type: string; excerpt: string; speaker: string; source_kind: string; occurred_at: string; filename: string | null } | null;
  task: TaskRef | null;
  evidence: { relation: string; strength: "claimed" | "verified"; rationale: string; type: string; excerpt: string; speaker: string; source_kind: string; occurred_at: string }[];
  history: { from: string | null; to: string; at: string; detail: Record<string, unknown> }[];
  pending_review_items: { id: number; kind: string }[];
  actions: { id: string; label: string; url?: string | null }[];
}
