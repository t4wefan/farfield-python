"""Opt-in modern Desktop IPC adapter, separate from the Farfield translation.

Protocol facts were checked against Remodex's September 26, 2026 bridge and
must be validated against the installed Codex App before production use.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import DesktopIpcError
from .ipc_client import DesktopIpcClient
from .protocol import parse_ipc_frame, parse_turn_start_params


METHOD_VERSIONS = {
    "initialize": 1,
    "thread-owner-discovery": 1,
    "thread-stream-following-changed": 1,
    "thread-stream-state-changed": 11,
    "thread-follower-load-complete-history": 1,
    "thread-follower-start-turn": 2,
    "thread-follower-update-thread-settings": 2,
    "thread-follower-steer-turn": 1,
    "thread-follower-interrupt-turn": 4,
    "thread-follower-compact-thread": 1,
    "thread-follower-command-approval-decision": 1,
    "thread-follower-file-approval-decision": 1,
    "thread-follower-permissions-request-approval-response": 1,
    "thread-follower-submit-user-input": 1,
    "thread-follower-submit-mcp-server-elicitation-response": 1,
}


def parse_modern_ipc_frame(value: object) -> dict:
    """Validate current Desktop frames, including router discovery envelopes.

    The historical Farfield schema expects a nested requestId on discovery
    requests; current routers put it on the outer envelope. Normalize only
    for validation and return the original frame unchanged.
    """
    if isinstance(value, dict) and value.get("type") == "client-discovery-request":
        request = value.get("request")
        if isinstance(request, dict) and "requestId" not in request:
            normalized = {**value, "request": {**request, "requestId": value.get("requestId")}}
            parse_ipc_frame(normalized)
            return value
    return parse_ipc_frame(value)


def ipc_socket_candidates(*, codex_home: str | None = None) -> list[str]:
    """Prefer the current Codex-home bus, then the legacy temporary bus."""
    if sys.platform == "win32":
        return [r"\\.\pipe\codex-ipc"]
    home = Path(os.path.abspath(os.path.expanduser(str(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex"))))
    return [str(home / "ipc" / "ipc.sock"), str(Path(tempfile.gettempdir()) / "codex-ipc" / f"ipc-{os.getuid()}.sock")]


class ModernDesktopIpcClient(DesktopIpcClient):
    def __init__(self, socket_paths: list[str] | None = None, *, request_timeout_ms: int = 10_000):
        paths = socket_paths if socket_paths is not None else ipc_socket_candidates()
        if not paths:
            raise ValueError("At least one IPC socket path is required")
        self.socket_paths = paths
        super().__init__(paths[0], request_timeout_ms=request_timeout_ms)

    async def connect(self) -> None:
        failures = []
        for path in self.socket_paths:
            self.socket_path = path
            try:
                await super().connect()
                return
            except DesktopIpcError as exc:
                failures.append(f"{path}: {exc}")
        raise DesktopIpcError("No Codex IPC socket accepted a connection: " + "; ".join(failures))

    def _parse_frame(self, value: object) -> dict:
        return parse_modern_ipc_frame(value)

    async def initialize(self, _user_agent: str, *, client_type: str = "aa-bridge") -> dict:
        return await self._request({
            "type": "request", "requestId": str(uuid4()),
            "sourceClientId": "initializing-client", "version": 1,
            "method": "initialize", "params": {"clientType": client_type},
        }, self.request_timeout_ms, "initialize")

    async def request(self, method: str, params: dict, *, owner_client_id: str | None = None, timeout_ms: int | None = None) -> dict:
        if method not in METHOD_VERSIONS:
            raise ValueError(f"Unknown Desktop IPC method version: {method}")
        return await self.send_request_and_wait(method, params, version=METHOD_VERSIONS[method],
                                                target_client_id=owner_client_id, timeout_ms=timeout_ms)

    async def discover_owner(self, thread_id: str, *, host_id: str = "local", no_owner_timeout_ms: int = 800) -> str | None:
        """Bound owner absence to one second. Never start a second writer on timeout."""
        try:
            response = await self.request("thread-owner-discovery", {"hostId": host_id, "conversationId": thread_id},
                                          timeout_ms=no_owner_timeout_ms)
        except DesktopIpcError as exc:
            if any(marker in str(exc) for marker in ("timed out", "no-client-found", "no-handler-for-request", "No Codex IPC client")):
                return None
            raise
        owner = response.get("handledByClientId")
        return owner if isinstance(owner, str) and owner and owner != self.client_id else None

    async def verify_owner(self, thread_id: str, owner_client_id: str, *, timeout_ms: int = 5_000) -> bool:
        try:
            response = await self.request("thread-follower-load-complete-history", {"conversationId": thread_id},
                                          owner_client_id=owner_client_id, timeout_ms=timeout_ms)
        except DesktopIpcError:
            return False
        return isinstance(response.get("result"), dict) and response["result"].get("revision") is not None

    async def follow(self, thread_id: str, *, host_id: str = "local", following: bool = True) -> None:
        await self.send_broadcast("thread-stream-following-changed", {
            "hostId": host_id, "conversationId": thread_id, "following": following,
        }, version=METHOD_VERSIONS["thread-stream-following-changed"])


class ModernCodexFollower:
    """Route writes to a verified Desktop owner; never replay uncertain writes."""

    def __init__(self, ipc: ModernDesktopIpcClient, *, owner_probe_timeout_ms: int = 800):
        self.ipc = ipc
        self.owner_probe_timeout_ms = owner_probe_timeout_ms

    async def _owner(self, thread_id: str) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.owner_probe_timeout_ms / 1000
        owner = await self.ipc.discover_owner(thread_id, no_owner_timeout_ms=self.owner_probe_timeout_ms)
        if owner is None:
            raise DesktopIpcError(f"No Codex Desktop owner for thread {thread_id}")
        remaining_ms = max(1, int((deadline - loop.time()) * 1000))
        if not await self.ipc.verify_owner(thread_id, owner, timeout_ms=remaining_ms):
            raise DesktopIpcError(f"Could not verify Codex Desktop owner for thread {thread_id}")
        return owner

    async def send_message(self, *, thread_id: str, text: str, template: dict | None = None,
                           model: str | None = None, effort: str | None = None,
                           collaboration_mode: dict | None = None) -> dict:
        text = text.strip()
        if not text:
            raise ValueError("Message text is required")
        owner = await self._owner(thread_id)
        params = {**(template or {}), "threadId": thread_id,
                  "input": [{"type": "text", "text": text}], "attachments": []}
        if model is not None:
            params["model"] = model
        if effort is not None:
            params["effort"] = effort
        if collaboration_mode is not None:
            params["collaborationMode"] = collaboration_mode
        parse_turn_start_params(params)
        settings: dict[str, Any] = {}
        if model is not None:
            settings["model"] = model
        if effort is not None:
            settings["effort"] = effort
        if collaboration_mode is not None:
            settings["collaborationMode"] = collaboration_mode
        if settings:
            await self.ipc.request("thread-follower-update-thread-settings", {
                "conversationId": thread_id, "threadSettings": settings,
            }, owner_client_id=owner)
        response = await self.ipc.request("thread-follower-start-turn", {
            "conversationId": thread_id,
            "turnStart": {"request": {**params, "clientUserMessageId": str(uuid4())},
                          "context": {"inheritThreadSettings": True}},
        }, owner_client_id=owner)
        return response.get("result", {})

    async def interrupt(self, *, thread_id: str, turn_id: str) -> None:
        owner = await self._owner(thread_id)
        await self.ipc.request("thread-follower-interrupt-turn", {
            "conversationId": thread_id, "mode": "user-stop", "expectedTurnId": turn_id,
        }, owner_client_id=owner)

    async def steer(self, *, thread_id: str, turn_id: str, text: str, cwd: str | None = None) -> dict:
        owner = await self._owner(thread_id)
        message_id = str(uuid4())
        response = await self.ipc.request("thread-follower-steer-turn", {
            "conversationId": thread_id,
            "input": [{"type": "text", "text": text}],
            "clientUserMessageId": message_id,
            "expectedTurnId": turn_id,
            "restoreMessage": {
                "id": message_id, "text": text, "cwd": cwd,
                "context": {"workspaceRoots": [cwd] if cwd else [], "commentAttachments": []},
                "createdAt": int(time.time() * 1000),
            },
        }, owner_client_id=owner)
        return response.get("result", {})

    async def compact(self, *, thread_id: str) -> dict:
        owner = await self._owner(thread_id)
        response = await self.ipc.request("thread-follower-compact-thread", {
            "conversationId": thread_id,
        }, owner_client_id=owner)
        return response.get("result", {})

    async def submit_approval(self, *, thread_id: str, request_id: str | int,
                              kind: str, response: dict) -> None:
        method = {
            "command": "thread-follower-command-approval-decision",
            "file": "thread-follower-file-approval-decision",
            "permissions": "thread-follower-permissions-request-approval-response",
            "user_input": "thread-follower-submit-user-input",
            "mcp_elicitation": "thread-follower-submit-mcp-server-elicitation-response",
        }.get(kind)
        if method is None:
            raise ValueError(f"Unknown approval kind: {kind}")
        owner = await self._owner(thread_id)
        payload = {"conversationId": thread_id, "requestId": request_id}
        payload["decision" if kind in {"command", "file"} else "response"] = response["decision"] if kind in {"command", "file"} else response
        await self.ipc.request(method, payload, owner_client_id=owner)
