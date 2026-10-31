"""Print the design decisions behind ProjectPulse and why each was made.

    python scripts/explain_decisions.py            # everything
    python scripts/explain_decisions.py --id D5    # one decision
    python scripts/explain_decisions.py --grep notion
"""
import argparse
import textwrap
from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    id: str
    title: str
    decision: str
    why: str
    rejected: str


DECISIONS = [
    Decision("D1", "Postgres is the source of truth",
             "Commitments, evidence and lifecycle history live in Postgres. LangGraph only manages workflow execution.",
             "The graph's checkpoints are an execution detail. Product state must survive a changed workflow and be queryable by the UI.",
             "Keeping product state inside LangGraph checkpoints."),
    Decision("D2", "Model interprets, backend validates, human resolves",
             "Muse Spark returns structured interpretations only. The backend validates every action and a human approves anything uncertain. Only the executor writes to Notion.",
             "A model that can both decide and act turns one misreading into a wrong write. Separating the roles keeps every write attributable and checkable.",
             "Letting the model call Notion directly through tools."),
    Decision("D3", "Ingest runs before transcription",
             "Hash the raw input and dedupe before calling Voice Transcribe.",
             "Otherwise a re-uploaded recording costs a transcription call before the duplicate is noticed.",
             "Transcribe first, then hash the text."),
    Decision("D4", "Retrieve context runs before extraction",
             "Load people, projects with aliases, and open tasks before Spark call 1.",
             "Extraction needs the roster to turn an anonymous speaker label into a name and to pick a project from aliases.",
             "Extract first, then look things up."),
    Decision("D5", "Match project first, then task",
             "Pick the project from the meeting context and aliases, then rank only that project's tasks.",
             "Two projects can hold near-identical task titles. Scoping by project prevents linking a commitment to the wrong one.",
             "A flat top-3 across every task."),
    Decision("D6", "Projects come from a Notion relation, not a rollup",
             "Tasks link to a Projects database through a relation; the sync reads the Projects database directly.",
             "A relation gives each task one real project record, replacing free-text names with typos. A rollup only copies a value and its text shape is undocumented.",
             "Fuzzy clustering of a free-text project column, or a rollup of aliases."),
    Decision("D7", "Reconcile in two passes",
             "Pass 1 handles commitments and requests. Pass 2 handles claims, progress and blockers against Notion tasks plus pass 1's results.",
             "A completion claim made in the same meeting as the commitment must be able to match it before it exists in Notion.",
             "A single pass over all events."),
    Decision("D8", "One interrupt per run, items batched",
             "The graph pauses once with all review items. Decisions are saved as they are made and an Apply step resumes the graph.",
             "Per-item interrupts add state and failure modes for no demo benefit.",
             "An interrupt for every review item."),
    Decision("D9", "Linking a commitment writes to the DB only",
             "Notion is written only when creating a task or resolving one.",
             "Fewer writes means a smaller blast radius and fewer idempotency cases.",
             "Writing a note to Notion on every approved link."),
    Decision("D10", "Claimed versus verified evidence",
             "A spoken claim is stored as claimed; independent evidence such as an email with the artifact is verified. Both only propose resolution.",
             "Someone saying they finished is not proof they finished. The product has to show the difference.",
             "Treating a model's reading of a claim as completion."),
    Decision("D11", "Progress and blockers skip review",
             "They attach as evidence automatically and never change a task's state.",
             "They change nothing authoritative, so asking a human is noise that buries the decisions that matter.",
             "Reviewing every extracted event."),
    Decision("D12", "Validate re-fetches Notion before executing",
             "Compare the page's last_edited_time with the snapshot. A newer edit fails validation and creates a new review item.",
             "A person may edit the task between the sync and the approval. The system must not overwrite them.",
             "Trusting the cached snapshot."),
    Decision("D13", "Execution is idempotent",
             "Every Notion write is keyed by a unique idempotency key in the executions table.",
             "Workflows retry. A retry must not apply the same approved action twice.",
             "Relying on the workflow never retrying."),
    Decision("D14", "Structured output plus backend validation",
             "Use json_schema response_format, then validate: excerpts must appear verbatim, deadlines must pass sanity checks.",
             "Schema-constrained output guarantees shape, not truth. Verbatim excerpts are checkable evidence and catch invented quotes.",
             "Trusting the model's excerpts and dates as returned."),
    Decision("D15", "Speaker names are inferred from context",
             "Diarization labels speakers A, B, C. Spark infers names from the conversation and the people roster, with a confidence.",
             "Voice Transcribe labels speakers but does not name them. 'Alex, can you...' followed by speaker E answering identifies E.",
             "Asking the user to label speakers by hand."),
    Decision("D16", "Dates: the model proposes, the backend checks",
             "Spark gives an ISO date and marks it explicit or inferred; the backend rejects dates before the meeting, over a year out, or with a mismatched weekday.",
             "Relative dates like 'next Wednesday' are ambiguous. Marking them inferred lets the UI flag them.",
             "Letting the model's date through unchecked."),
    Decision("D17", "Audio first; email gate deferred",
             "The email work-relevance check from the sketch is out of the first build.",
             "Demo emails are curated, so the gate adds a model call that proves nothing. Time is better spent on the core loop.",
             "Building every input path at once."),
    Decision("D18", "Synthetic demo audio",
             "The demo meeting is generated from a scripted dialogue with distinct TTS voices.",
             "The demo needs several speakers, repeatably, with known content. It also shows diarization separating five voices.",
             "A hand-recorded single-voice file."),
    Decision("D19", "An unnamed speaker stays unnamed",
             "When nothing in the conversation identifies a speaker, Spark leaves the name blank with low confidence and the backend does not guess. The owner becomes an ambiguous-owner review item.",
             "In the live run Spark named speaker E (addressed by name, then answered) but declined to name A to D, whom nobody addressed. A wrong owner on a commitment is worse than a blank one a human fills in.",
             "Filling the gap by guessing from the task list or from voice order."),
]


def render(d: Decision) -> str:
    wrap = lambda label, text: textwrap.fill(text, 92, initial_indent=f"  {label:<10}", subsequent_indent=" " * 12)
    return "\n".join([f"{d.id}  {d.title}", wrap("Decision:", d.decision), wrap("Why:", d.why), wrap("Not:", d.rejected)])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", help="show one decision, e.g. D5")
    ap.add_argument("--grep", help="show decisions whose text contains this (case-insensitive)")
    args = ap.parse_args()

    chosen = DECISIONS
    if args.id:
        chosen = [d for d in DECISIONS if d.id.lower() == args.id.lower()]
    if args.grep:
        needle = args.grep.lower()
        chosen = [d for d in chosen if needle in render(d).lower()]
    if not chosen:
        raise SystemExit("no matching decisions")
    print("\n\n".join(render(d) for d in chosen))


if __name__ == "__main__":
    main()
