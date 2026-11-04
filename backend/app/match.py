"""Match: shortlist candidate Notion tasks for an event. Deterministic; Spark only judges the shortlist.

Project first: the meeting's project (or an event's own hint) scopes which tasks are candidates at all,
so a near-identical task in another project can never be linked by mistake.
"""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

DONE_STATUSES = {"done", "completed"}
MIN_SCORE = 0.25
TOP_K = 3
OWNER_BONUS = 0.25

_STOP = {
    "the", "a", "an", "to", "for", "of", "and", "on", "in", "at", "by", "with", "send", "sent", "finalize",
    "prepare", "confirm", "get", "ready", "is", "are", "it", "we", "i",
    "so", "has", "have", "been", "was", "were", "that", "this", "there", "their", "its", "also", "still",
}


@dataclass(frozen=True)
class Candidate:
    page_id: str
    title: str
    owner: str
    due: str
    status: str
    score: float


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP}


def _same(a: str, b: str) -> bool:
    """Words match when equal or when they share a stem of five letters: deliver, delivered, delivery."""
    return a == b or (min(len(a), len(b)) >= 5 and a[:5] == b[:5])


def score_task(deliverable: str, owner: str | None, task: dict) -> float:
    """0..1 similarity between what an event is about and a task. Owner agreement adds a bonus; an unknown owner neither helps nor hurts."""
    want, have = _tokens(deliverable), _tokens(task["title"])
    overlap = sum(any(_same(w, h) for h in have) for w in want) / len(want) if want else 0.0
    seq = SequenceMatcher(None, deliverable.lower(), task["title"].lower()).ratio()
    score = 0.65 * overlap + 0.35 * seq
    if owner and task["owner_name"] and owner.lower() == task["owner_name"].lower():
        score += OWNER_BONUS
    return min(score, 1.0)


def resolve_project(hint: str | None, meeting_project: str | None, projects: list[dict]) -> int | None:
    """An event's own hint wins over the meeting's project. Unknown or unmatched names give None."""
    by_name = {p["name"].lower(): p["id"] for p in projects}
    for name in (hint, meeting_project):
        if name and name.lower() in by_name:
            return by_name[name.lower()]
    return None


def candidates_for(deliverable: str, owner: str | None, tasks: list[dict], project_id: int | None) -> list[Candidate]:
    """Top candidates among open tasks in the project. With no project, search every project."""
    pool = [
        t for t in tasks
        if t["status"].lower() not in DONE_STATUSES and (project_id is None or t["project_id"] == project_id)
    ]
    scored = sorted(((score_task(deliverable, owner, t), t) for t in pool), key=lambda x: -x[0])
    return [
        Candidate(t["notion_page_id"], t["title"], t["owner_name"], str(t["due_date"] or ""), t["status"], round(s, 2))
        for s, t in scored[:TOP_K] if s >= MIN_SCORE
    ]


RESTATEMENT_MIN = 0.4


def restates_finished_work(deliverable: str, owner: str | None, tasks: list[dict], project_id: int | None) -> bool:
    """True when a statement is best explained by a task that is already Done, not by any open one.

    Meetings repeat finished work ("the display has been delivered"). That needs no review: nothing is left to do.
    """
    in_project = [t for t in tasks if project_id is None or t["project_id"] == project_id]
    best = lambda pool: max((score_task(deliverable, owner, t) for t in pool), default=0.0)
    done = best([t for t in in_project if t["status"].lower() in DONE_STATUSES])
    open_ = best([t for t in in_project if t["status"].lower() not in DONE_STATUSES])
    return done >= RESTATEMENT_MIN and done > open_
