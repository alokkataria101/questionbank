"""Question lifecycle. Nothing is published without an explicit human approval."""
from __future__ import annotations

STATUSES = ["uploaded", "processing", "draft", "under_review",
            "revision_requested", "approved", "published"]

# action -> (allowed from, resulting status, roles allowed)
ACTIONS: dict[str, tuple[set[str], str, set[str]]] = {
    "submit":            ({"draft", "revision_requested"}, "under_review", {"typist", "teacher", "admin"}),
    "request_revision":  ({"under_review"}, "revision_requested", {"teacher", "admin"}),
    "approve":           ({"under_review"}, "approved", {"teacher", "admin"}),
    "publish":           ({"approved"}, "published", {"teacher", "admin"}),
    "unpublish":         ({"published"}, "under_review", {"teacher", "admin"}),
}

EDITABLE = {"draft", "under_review", "revision_requested"}


class WorkflowError(Exception):
    pass


def next_status(action: str, current: str, role: str) -> str:
    if action not in ACTIONS:
        raise WorkflowError(f"unknown action {action!r}")
    allowed_from, result, roles = ACTIONS[action]
    if role not in roles:
        raise WorkflowError(f"role {role!r} may not {action}")
    if current not in allowed_from:
        raise WorkflowError(f"cannot {action} a question that is {current!r}")
    return result
