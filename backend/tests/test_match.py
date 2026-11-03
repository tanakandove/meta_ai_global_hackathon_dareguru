from datetime import date

from app.match import candidates_for, resolve_project, score_task

MID, HARBOR, EAST = 1, 2, 3


def task(pid, title, owner, project, status="Not-started", due=date(2026, 11, 6)):
    return {"notion_page_id": pid, "title": title, "owner_name": owner, "project_id": project, "status": status, "due_date": due}


TASKS = [
    task("mid-specs", "Send revised hardware specifications for facilities", "Dana", MID),
    task("mid-cabling", "Finalize cabling and network-drop plan", "Priya", MID),
    task("mid-nuc", "Prepare NUC configuration and deployment checklist", "Alex", MID),
    task("harbor-specs", "Send revised hardware specifications for kiosks", "Dana", HARBOR),
    task("east-cabling", "Finalize cabling and network-drop plan", "Priya", EAST),
]
PROJECTS = [{"id": MID, "name": "MidTown Community Dashboard"}, {"id": HARBOR, "name": "Harbor Library Kiosks"}]


def ids(cands):
    return [c.page_id for c in cands]


def test_best_match_is_the_same_deliverable():
    assert ids(candidates_for("revised hardware specifications", "Dana", TASKS, MID))[0] == "mid-specs"


def test_project_scoping_excludes_lookalike_tasks_in_other_projects():
    found = ids(candidates_for("cabling plan", "Priya", TASKS, MID))
    assert "mid-cabling" in found and "east-cabling" not in found
    found = ids(candidates_for("revised hardware specifications", "Dana", TASKS, MID))
    assert "harbor-specs" not in found


def test_without_a_project_every_project_is_searched():
    assert {"mid-cabling", "east-cabling"} <= set(ids(candidates_for("cabling plan", None, TASKS, None)))


def test_done_tasks_are_never_candidates():
    tasks = TASKS + [task("done", "Finalize cabling plan", "Priya", MID, status="Done")]
    assert "done" not in ids(candidates_for("cabling plan", "Priya", tasks, MID))


def test_owner_agreement_raises_the_score_and_unknown_owner_is_neutral():
    t = TASKS[0]
    assert score_task("hardware specifications", "Dana", t) > score_task("hardware specifications", "Sam", t)
    assert score_task("hardware specifications", None, t) == score_task("hardware specifications", "Sam", t)


def test_shortlist_is_capped_at_three_and_drops_unrelated_tasks():
    many = [task(f"t{i}", f"Cabling plan part {i}", "Priya", MID) for i in range(6)]
    assert len(candidates_for("cabling plan", None, many, MID)) == 3
    assert candidates_for("order catering", None, TASKS, MID) == []


def test_event_hint_beats_meeting_project():
    assert resolve_project("Harbor Library Kiosks", "MidTown Community Dashboard", PROJECTS) == HARBOR
    assert resolve_project(None, "midtown community dashboard", PROJECTS) == MID
    assert resolve_project(None, "unknown", PROJECTS) is None
