-- ProjectPulse schema. Postgres is the source of truth for commitments,
-- evidence and lifecycle history; LangGraph only manages workflow execution.
-- Idempotent: safe to run repeatedly.

CREATE TABLE IF NOT EXISTS projects (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    aliases     TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS people (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    aliases     TEXT[] NOT NULL DEFAULT '{}'
);

-- Snapshot of the Notion tasks database. notion_last_edited is compared before
-- any write so a stale snapshot can never overwrite a newer human edit.
CREATE TABLE IF NOT EXISTS notion_tasks (
    notion_page_id      TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    owner_name          TEXT NOT NULL DEFAULT '',
    due_date            DATE,
    status              TEXT NOT NULL DEFAULT '',
    project_name        TEXT NOT NULL DEFAULT '',
    evidence_text       TEXT NOT NULL DEFAULT '',
    person_id           BIGINT REFERENCES people(id),
    project_id          BIGINT REFERENCES projects(id),
    notion_last_edited  TIMESTAMPTZ NOT NULL,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per ingested input: a recording, transcript, email or text update.
CREATE TABLE IF NOT EXISTS sources (
    id              BIGSERIAL PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('audio', 'transcript', 'email', 'text')),
    occurred_at     TIMESTAMPTZ NOT NULL,
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    raw_text        TEXT NOT NULL,
    audio_filename  TEXT,
    turns           JSONB,
    content_hash    TEXT NOT NULL UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Structured items Muse Spark extracted from a source. Never authoritative:
-- an event only becomes a commitment through reconciliation and review.
CREATE TABLE IF NOT EXISTS events (
    id              BIGSERIAL PRIMARY KEY,
    source_id       BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    type            TEXT NOT NULL CHECK (type IN (
                        'commitment', 'request', 'decision', 'progress',
                        'completion_claim', 'blocker', 'suggestion')),
    speaker_label   TEXT,
    speaker_name    TEXT,
    owner_name      TEXT,
    deliverable     TEXT NOT NULL,
    deadline_text   TEXT NOT NULL DEFAULT '',
    deadline_date   DATE,
    deadline_basis  TEXT NOT NULL DEFAULT 'none' CHECK (deadline_basis IN ('explicit', 'inferred', 'none')),
    excerpt         TEXT NOT NULL,
    turn_index      INT,
    start_ms        INT,
    end_ms          INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_source_idx ON events(source_id);

-- Open loops. State machine:
--   proposed -> open -> resolution_proposed -> resolved
--   proposed -> rejected;  resolution_proposed -> open (resolution rejected)
CREATE TABLE IF NOT EXISTS commitments (
    id                  BIGSERIAL PRIMARY KEY,
    state               TEXT NOT NULL DEFAULT 'proposed' CHECK (state IN (
                            'proposed', 'open', 'resolution_proposed', 'resolved', 'rejected')),
    owner_name          TEXT,
    deliverable         TEXT NOT NULL,
    deadline_date       DATE,
    deadline_basis      TEXT NOT NULL DEFAULT 'none' CHECK (deadline_basis IN ('explicit', 'inferred', 'none')),
    project_id          BIGINT REFERENCES projects(id),
    origin_event_id     BIGINT REFERENCES events(id),
    notion_page_id      TEXT REFERENCES notion_tasks(notion_page_id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS commitments_state_idx ON commitments(state);

-- An event bearing on a commitment. 'claimed' means someone said it;
-- 'verified' means independent evidence exists (e.g. an email with the artifact).
CREATE TABLE IF NOT EXISTS evidence (
    id              BIGSERIAL PRIMARY KEY,
    commitment_id   BIGINT NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
    event_id        BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL CHECK (relation IN (
                        'origin', 'completion', 'progress', 'blocker', 'contradiction')),
    strength        TEXT NOT NULL DEFAULT 'claimed' CHECK (strength IN ('claimed', 'verified')),
    rationale       TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (commitment_id, event_id, relation)
);

-- Candidate links between an event and an existing Notion task.
CREATE TABLE IF NOT EXISTS task_matches (
    id              BIGSERIAL PRIMARY KEY,
    event_id        BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    notion_page_id  TEXT NOT NULL REFERENCES notion_tasks(notion_page_id),
    score           REAL NOT NULL,
    rationale       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'confirmed', 'rejected')),
    UNIQUE (event_id, notion_page_id)
);

-- Items awaiting a human decision.
CREATE TABLE IF NOT EXISTS review_items (
    id                  BIGSERIAL PRIMARY KEY,
    kind                TEXT NOT NULL CHECK (kind IN (
                            'uncertain_extraction', 'ambiguous_owner_or_deadline', 'task_match',
                            'possible_duplicate', 'conflict', 'potential_completion', 'suggestion')),
    reason              TEXT NOT NULL,
    source_id           BIGINT REFERENCES sources(id),
    event_id            BIGINT REFERENCES events(id),
    commitment_id       BIGINT REFERENCES commitments(id),
    proposed_action     JSONB NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    graph_thread_id     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS review_items_status_idx ON review_items(status);

CREATE TABLE IF NOT EXISTS decisions (
    id              BIGSERIAL PRIMARY KEY,
    review_item_id  BIGINT NOT NULL REFERENCES review_items(id),
    action          TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'edit', 'clarify')),
    edits           JSONB NOT NULL DEFAULT '{}',
    decided_by      TEXT NOT NULL DEFAULT 'user',
    decided_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every Notion write goes through here. The unique idempotency key means a
-- retried workflow cannot apply the same approved action twice.
CREATE TABLE IF NOT EXISTS executions (
    id                  BIGSERIAL PRIMARY KEY,
    idempotency_key     TEXT NOT NULL UNIQUE,
    action_type         TEXT NOT NULL CHECK (action_type IN ('create_task', 'update_task')),
    notion_page_id      TEXT,
    payload             JSONB NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done', 'failed')),
    result              JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only record of state transitions.
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id   BIGINT NOT NULL,
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}',
    at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_entity_idx ON audit_log(entity_type, entity_id);
