"""Strict patch and stream reducer translated from @farfield/api/live-state."""

from __future__ import annotations

import json
from typing import Any

from .protocol import parse_thread_conversation_state, parse_thread_stream_patch, parse_thread_stream_state_changed_broadcast


class ThreadStreamReductionError(Exception):
    def __init__(self, message: str, details: dict, cause: Exception | None = None):
        super().__init__(message)
        self.details = details
        self.__cause__ = cause


def apply_strict_patch(source: dict, patch: dict) -> dict:
    patch = parse_thread_stream_patch(patch)
    state = json.loads(json.dumps(source))
    path = patch["path"]
    if not path:
        raise ValueError("Patch path cannot be empty")
    parent: Any = state
    for segment in path[:-1]:
        if isinstance(parent, list) and type(segment) is int:
            if segment < 0 or segment >= len(parent):
                raise ValueError(f"Patch path segment out of range: [{segment}]")
            parent = parent[segment]
        elif isinstance(parent, dict) and isinstance(segment, str):
            if segment not in parent:
                raise ValueError(f"Patch path segment missing: {segment}")
            parent = parent[segment]
        else:
            raise ValueError(f"Patch path invalid at segment {segment}")
    last = path[-1]
    op = patch["op"]
    if isinstance(parent, list) and type(last) is int:
        if op == "add":
            # JS Array.splice clamps indices larger than the array length.
            parent.insert(min(last, len(parent)), patch["value"])
        elif op == "replace":
            if last >= len(parent):
                raise ValueError(f"Patch replace index out of range: {last}")
            parent[last] = patch["value"]
        else:
            if last >= len(parent):
                raise ValueError(f"Patch remove index out of range: {last}")
            parent.pop(last)
    elif isinstance(parent, dict) and isinstance(last, str):
        if op == "remove":
            if last not in parent:
                raise ValueError(f"Patch remove key missing: {last}")
            del parent[last]
        else:
            parent[last] = patch["value"]
    else:
        raise ValueError("Patch target type mismatch")
    return parse_thread_conversation_state(state)


def reduce_thread_stream_events(events: list[dict]) -> dict[str, dict]:
    by_thread: dict[str, dict] = {}
    for event_index, raw in enumerate(events):
        event = parse_thread_stream_state_changed_broadcast(raw)
        thread_id = event["params"]["conversationId"]
        prior = by_thread.get(thread_id, {"ownerClientId": None, "conversationState": None})
        next_state = {"ownerClientId": event["sourceClientId"], "conversationState": prior["conversationState"]}
        change = event["params"]["change"]
        if change["type"] == "snapshot":
            next_state["conversationState"] = change["conversationState"]
            by_thread[thread_id] = next_state
            continue
        if next_state["conversationState"] is None:
            raise ValueError(f"Thread stream reduction failed for thread {thread_id} at event {event_index}: patch event arrived before snapshot")
        updated = next_state["conversationState"]
        for patch_index, patch in enumerate(change["patches"]):
            try:
                updated = apply_strict_patch(updated, patch)
            except Exception as exc:
                details = {"threadId": thread_id, "eventIndex": event_index, "patchIndex": patch_index, "event": event, "patch": patch}
                raise ThreadStreamReductionError(f"Thread stream reduction failed for thread {thread_id} at event {event_index}, patch {patch_index}: {exc}", details, exc) from exc
        next_state["conversationState"] = updated
        by_thread[thread_id] = next_state
    return by_thread


def find_latest_turn_params_template(conversation_state: dict) -> dict:
    for turn in reversed(conversation_state["turns"]):
        if turn.get("params"):
            return turn["params"]
    raise ValueError("No turn params template found in conversation state")
