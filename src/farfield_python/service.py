"""Farfield's follower actions over DesktopIpcClient."""

from __future__ import annotations

from typing import Any

from .protocol import (parse_command_execution_request_approval_response,
                       parse_file_change_request_approval_response,
                       parse_tool_request_user_input_response_payload)


class CodexMonitorService:
    def __init__(self, ipc_client: Any):
        self.ipc_client = ipc_client

    @staticmethod
    def _route(owner_client_id: str) -> dict:
        return {"target_client_id": owner_client_id, "version": 1}

    async def send_message(self, *, thread_id: str, owner_client_id: str, text: str,
                           cwd: str | None = None, is_steering: bool = False,
                           turn_start_template: dict | None = None,
                           model: str | None = None, effort: str | None = None,
                           collaboration_mode: dict | None = None) -> None:
        text = text.strip()
        if not text:
            raise ValueError("Message text is required")
        template = turn_start_template
        params = {**template, "threadId": thread_id, "input": [{"type": "text", "text": text}],
                  "attachments": template.get("attachments") if isinstance(template.get("attachments"), list) else []} if template else {
                      "threadId": thread_id, "input": [{"type": "text", "text": text}], "attachments": []}
        if cwd is not None:
            params["cwd"] = cwd
        for key, value in (("model", model), ("effort", effort), ("collaborationMode", collaboration_mode)):
            if value is not None:
                params[key] = value
        await self.ipc_client.send_request_and_wait("thread-follower-start-turn", {
            "conversationId": thread_id, "turnStartParams": params, "isSteering": bool(is_steering)
        }, **self._route(owner_client_id))

    async def set_collaboration_mode(self, *, thread_id: str, owner_client_id: str, collaboration_mode: dict) -> None:
        await self.ipc_client.send_request_and_wait("thread-follower-set-collaboration-mode", {
            "conversationId": thread_id, "collaborationMode": collaboration_mode
        }, **self._route(owner_client_id))

    async def submit_user_input(self, *, thread_id: str, owner_client_id: str, request_id: str | int, response: dict) -> None:
        payload = parse_tool_request_user_input_response_payload(response)
        await self.ipc_client.send_request_and_wait("thread-follower-submit-user-input", {
            "conversationId": thread_id, "requestId": request_id, "response": payload
        }, **self._route(owner_client_id))

    async def submit_command_approval_decision(self, *, thread_id: str, owner_client_id: str, request_id: str | int, response: dict) -> None:
        payload = parse_command_execution_request_approval_response(response)
        await self.ipc_client.send_request_and_wait("thread-follower-command-approval-decision", {
            "conversationId": thread_id, "requestId": request_id, "decision": payload["decision"]
        }, **self._route(owner_client_id))

    async def submit_file_approval_decision(self, *, thread_id: str, owner_client_id: str, request_id: str | int, response: dict) -> None:
        payload = parse_file_change_request_approval_response(response)
        await self.ipc_client.send_request_and_wait("thread-follower-file-approval-decision", {
            "conversationId": thread_id, "requestId": request_id, "decision": payload["decision"]
        }, **self._route(owner_client_id))

    async def interrupt(self, *, thread_id: str, owner_client_id: str) -> None:
        await self.ipc_client.send_request_and_wait("thread-follower-interrupt-turn", {
            "conversationId": thread_id
        }, **self._route(owner_client_id))
