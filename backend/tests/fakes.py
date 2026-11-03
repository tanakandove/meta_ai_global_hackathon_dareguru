"""An in-memory stand-in for the Notion gateway, with hooks to simulate failures and outside edits."""
import uuid
from dataclasses import replace
from datetime import date, datetime, timezone

from app.notion_api import DONE_STATUS, NOT_STARTED, NotionTask


def now() -> datetime:
    return datetime.now(timezone.utc)


class FakeGateway:
    def __init__(self):
        self.pages: dict[str, NotionTask] = {}
        self.calls: list[str] = []
        self.fail_times: dict[str, int] = {}          # method name -> how many calls should raise
        self.fail_after_create = 0                    # create succeeds remotely, then the call raises

    def add(self, page_id, title, owner, due, status, project_page_id, project="") -> NotionTask:
        task = NotionTask(page_id, title, owner, due, status, project, project_page_id, "", now())
        self.pages[page_id] = task
        return task

    def _maybe_fail(self, method: str) -> None:
        self.calls.append(method)
        if self.fail_times.get(method, 0) > 0:
            self.fail_times[method] -= 1
            raise RuntimeError(f"simulated {method} failure")

    def get_task(self, page_id):
        self._maybe_fail("get_task")
        return self.pages.get(page_id)

    def find_by_marker(self, marker):
        self.calls.append("find_by_marker")
        return next((p.page_id for p in self.pages.values() if marker in p.evidence), None)

    def create_task(self, *, marker, title, owner, due, project_name, project_page_id, evidence):
        self.calls.append("create_task")
        page_id = "fake-" + uuid.uuid4().hex[:8]
        self.pages[page_id] = NotionTask(page_id, title, owner or "", date.fromisoformat(due) if due else None,
                                         NOT_STARTED, project_name, project_page_id, f"{evidence} [{marker}]", now())
        if self.fail_after_create > 0:
            self.fail_after_create -= 1
            raise RuntimeError("simulated crash after the remote write succeeded")
        return page_id

    def mark_done(self, page_id, *, marker, note):
        self._maybe_fail("mark_done")
        self.calls.append("mark_done_applied")
        p = self.pages[page_id]
        evidence = p.evidence if marker in p.evidence else f"{p.evidence}\n{note} [{marker}]".strip()
        self.pages[page_id] = replace(p, status=DONE_STATUS, evidence=evidence, last_edited=now())

    def edit_externally(self, page_id, **changes) -> None:
        """A person edits the task in Notion between the sync and the approval."""
        self.pages[page_id] = replace(self.pages[page_id], last_edited=now(), **changes)
