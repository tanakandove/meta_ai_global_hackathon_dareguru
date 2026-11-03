"""The HTTP layer a UI talks to, driven through the real endpoints with a fake model and Notion."""
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import queries
from app.api import app
from tests.test_graph import P, TEXT, TZ, fast_retries, pool, world  # noqa: F401  (shared fixtures)


@pytest.fixture
def client(world):  # noqa: F811
    graph, _ = world["make_graph"]()
    app.state.graph = graph
    yield TestClient(app)
    app.state.graph = None


def new_run(client, **overrides):
    form = {"kind": "transcript", "meeting_date": "2026-10-31", "meeting_time": "10:00", "tz": TZ, "text": TEXT, **overrides}
    return client.post("/api/runs", data=form)


def wait_for(client, thread_id, *statuses, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        run = client.get(f"/api/runs/{thread_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.05)
    raise AssertionError(f"run stayed {run['status']} ({run['message']}), wanted {statuses}")


def test_the_whole_journey_over_http(client):
    r = new_run(client)
    assert r.status_code == 202
    tid = r.json()["thread_id"]

    run = wait_for(client, tid, "waiting_for_review")
    assert run["can_apply"] is False and len(run["items"]) == 3
    assert {i["kind"] for i in run["items"]} == {"task_match", "suggestion", "potential_completion"}

    # The inbox carries what a reviewer needs: the quote, the reason, the proposed action, the task.
    (inbox_run,) = [x for x in client.get("/api/inbox").json() if x["thread_id"] == tid]
    link = next(i for i in inbox_run["items"] if i["kind"] == "task_match")
    assert link["event"]["excerpt"].startswith("I'll send the revised hardware specifications")
    assert link["task"]["title"].startswith("Send revised hardware specifications") and link["action"]["type"] == "link_task"
    assert link["alternatives"]  # the reviewer can pick a different task

    refused = client.post(f"/api/runs/{tid}/apply")
    assert refused.status_code == 409 and "still need a decision" in refused.json()["detail"]

    for item in run["items"]:
        assert client.post(f"/api/review/{item['id']}/decision", json={"action": "approve"}).status_code == 200
    after = client.get(f"/api/runs/{tid}").json()
    assert after["can_apply"] is True and after["undecided_item_ids"] == []

    assert client.post(f"/api/runs/{tid}/apply").status_code == 202
    done = wait_for(client, tid, "completed")
    assert all(i["status"] == "resolved" for i in done["items"])

    follow = [c for c in client.get("/api/followups").json()["commitments"] if c["project"] == P + "MidTown"]
    states = {c["title"]: c["state"] for c in follow}  # a linked commitment is titled by its Notion task
    assert states["Finalize cabling and network-drop plan"] == "resolved"
    assert states["Send revised hardware specifications for facilities"] == "open"

    cabling = next(c for c in follow if c["state"] == "resolved")
    detail = client.get(f"/api/commitments/{cabling['id']}").json()
    assert detail["task"]["status"] == "Done" and detail["owner"] == "Priya"
    assert [(e["relation"], e["strength"]) for e in detail["evidence"]] == [("completion", "claimed")]
    assert [h["to"] for h in detail["history"]] == ["open", "resolution_proposed", "resolved"]


def test_bad_input_gets_a_message_a_person_can_act_on(client):
    assert "kind" in new_run(client, kind="fax").json()["detail"]
    assert "timezone" in new_run(client, tz="Mars/Base").json()["detail"].lower()
    assert "Paste some text" in new_run(client, text="  ").json()["detail"]
    assert "WAV" in client.post("/api/runs", data={"kind": "audio", "meeting_date": "2026-10-31", "tz": TZ}).json()["detail"]
    bad = client.post("/api/runs", data={"kind": "audio", "meeting_date": "2026-10-31", "tz": TZ},
                      files={"file": ("memo.wav", b"not audio at all", "audio/wav")})
    assert bad.status_code == 400 and "readable WAV" in bad.json()["detail"]
    assert client.get("/api/runs/run-nope").status_code == 404
    assert client.get("/api/commitments/999999").status_code == 404
    assert client.post("/api/review/999999/decision", json={"action": "approve"}).status_code == 409


def test_edit_needs_a_change_and_unknown_fields_are_refused(client):
    tid = new_run(client).json()["thread_id"]
    item = wait_for(client, tid, "waiting_for_review")["items"][0]
    assert client.post(f"/api/review/{item['id']}/decision", json={"action": "edit", "edits": {}}).status_code == 409
    assert client.post(f"/api/review/{item['id']}/decision", json={"action": "edit", "edits": {"status": "Done"}}).status_code == 409
    assert client.post(f"/api/review/{item['id']}/decision", json={"action": "edit", "edits": {"due": "soon"}}).status_code == 409


def test_followup_flags_group_work_for_the_center(client):
    tid = new_run(client).json()["thread_id"]
    run = wait_for(client, tid, "waiting_for_review")
    for item in run["items"]:
        client.post(f"/api/review/{item['id']}/decision", json={"action": "approve"})
    client.post(f"/api/runs/{tid}/apply")
    wait_for(client, tid, "completed")

    by = {c["title"]: c for c in queries.followups(today=date(2026, 11, 4))["commitments"] if c["project"] == P + "MidTown"}
    mount = by["Confirm display mounting locations and measurements"]
    assert mount["is_overdue"] and mount["is_blocked"] and not mount["needs_approval"]     # due Nov 2, blocker evidence
    specs = by["Send revised hardware specifications for facilities"]
    assert specs["is_upcoming"] and not specs["is_overdue"]                                 # due Nov 6
    assert not by["Finalize cabling and network-drop plan"]["is_overdue"]                   # resolved work is never overdue


def test_rerun_over_http_is_refused_once_a_run_is_applied(client):
    tid = new_run(client).json()["thread_id"]
    run = wait_for(client, tid, "waiting_for_review")
    for item in run["items"]:
        client.post(f"/api/review/{item['id']}/decision", json={"action": "approve"})
    client.post(f"/api/runs/{tid}/apply")
    wait_for(client, tid, "completed")
    refused = client.post(f"/api/runs/{tid}/rerun")
    assert refused.status_code == 409 and "waiting for review" in refused.json()["detail"]
    assert any(p["name"] == P + "MidTown" for p in client.get("/api/projects").json())
