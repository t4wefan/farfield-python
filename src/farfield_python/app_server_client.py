"""High-level Codex app-server requests, matching Farfield's AppServerClient."""

from __future__ import annotations

from typing import Any

from .app_server_transport import ChildProcessAppServerTransport
from .protocol import (
    _field, _issue, _obj, parse_app_server_collaboration_mode_list_response,
    parse_app_server_get_account_rate_limits_response, parse_app_server_list_models_response,
    parse_app_server_list_threads_response, parse_app_server_read_thread_result,
    parse_app_server_start_thread_response, parse_generated, parse_turn_start_params,
    parse_user_input_response_payload,
)


class AppServerClient:
    def __init__(self, transport: Any = None, **transport_options: Any):
        self.transport = transport if transport is not None else ChildProcessAppServerTransport(**transport_options)

    async def close(self) -> None:
        await self.transport.close()

    def on_server_notification(self, listener):
        return self.transport.on_server_notification(listener)

    def on_server_request(self, listener):
        return self.transport.on_server_request(listener)

    async def list_threads(self, *, limit: int, archived: bool, cursor: str | None = None) -> dict:
        result = await self.transport.request("thread/list", {"limit": limit, "archived": archived, "cursor": cursor})
        return parse_app_server_list_threads_response(result)

    async def list_loaded_threads(self, *, limit: int | None = None, cursor: str | None = None) -> dict:
        result = _obj(await self.transport.request("thread/loaded/list", {"limit": limit, "cursor": cursor}), "AppServerLoadedThreadListResponse")
        for item in _field(result, "data", "list", "AppServerLoadedThreadListResponse"):
            if not isinstance(item, str) or not item:
                _issue("AppServerLoadedThreadListResponse", "data", "Expected nonempty string")
        _field(result, "nextCursor", "str|null", "AppServerLoadedThreadListResponse", required=False)
        return result

    async def list_threads_all(self, *, limit: int, archived: bool, max_pages: int, cursor: str | None = None) -> dict:
        items: list = []
        pages = 0
        while pages < max_pages:
            page = await self.list_threads(limit=limit, archived=archived, cursor=cursor)
            items.extend(page["data"])
            pages += 1
            next_cursor = page.get("nextCursor")
            if not next_cursor or not page["data"]:
                return {"data": items, "nextCursor": None, "pages": pages, "truncated": False}
            cursor = next_cursor
        return {"data": items, "nextCursor": cursor, "pages": pages, "truncated": True}

    async def read_thread(self, thread_id: str, include_turns: bool = True) -> dict:
        result = await self.transport.request("thread/read", {"threadId": thread_id, "includeTurns": include_turns})
        return parse_app_server_read_thread_result(result)

    async def list_models(self, limit: int = 100) -> dict:
        return parse_app_server_list_models_response(await self.transport.request("model/list", {"limit": limit}))

    async def list_collaboration_modes(self) -> dict:
        return parse_app_server_collaboration_mode_list_response(await self.transport.request("collaborationMode/list", {}))

    async def read_account_rate_limits(self) -> dict:
        return parse_app_server_get_account_rate_limits_response(await self.transport.request("account/rateLimits/read", {}))

    async def start_thread(self, *, cwd: str, model: str | None = None, model_provider: str | None = None,
                           personality: str | None = None, sandbox: str | None = None,
                           approval_policy: str | None = None, ephemeral: bool = False) -> dict:
        request = {"cwd": cwd, "ephemeral": ephemeral}
        for key, value in (("model", model), ("modelProvider", model_provider), ("personality", personality),
                           ("sandbox", sandbox), ("approvalPolicy", approval_policy)):
            if value is not None:
                request[key] = value
        parse_generated(request, "v2/ThreadStartParams", context="AppServerStartThreadRequest")
        return parse_app_server_start_thread_response(await self.transport.request("thread/start", request))

    async def send_user_message(self, thread_id: str, text: str) -> None:
        request = {"threadId": thread_id, "input": [{"type": "text", "text": text}], "attachments": []}
        parse_turn_start_params(request)
        await self.transport.request("turn/start", request)

    async def start_turn(self, params: dict) -> None:
        parse_turn_start_params(params)
        mode = params.get("collaborationMode")
        if mode is not None and (not isinstance(mode["settings"].get("model"), str) or not mode["settings"]["model"].strip()):
            _issue("AppServerTurnStartRequest", "collaborationMode.settings.model", "collaborationMode.settings.model is required")
        await self.transport.request("turn/start", params)

    async def steer_turn(self, *, thread_id: str, expected_turn_id: str, input: list[dict]) -> None:
        request = {"threadId": thread_id, "expectedTurnId": expected_turn_id, "input": input}
        parse_turn_start_params({"threadId": thread_id, "input": input})
        if not expected_turn_id:
            _issue("AppServerTurnSteerRequest", "expectedTurnId", "Required")
        await self.transport.request("turn/steer", request)

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        if not thread_id or not turn_id:
            _issue("AppServerTurnInterruptRequest", "", "Expected nonempty threadId and turnId")
        await self.transport.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    async def submit_user_input(self, request_id: str | int, response: dict) -> None:
        if not isinstance(request_id, str) and type(request_id) is not int:
            _issue("UserInputRequestId", "", "Expected string or integer")
        await self.transport.respond(request_id, parse_user_input_response_payload(response))

    async def resume_thread(self, thread_id: str, *, persist_extended_history: bool = True) -> dict:
        if not thread_id or type(persist_extended_history) is not bool:
            _issue("AppServerResumeThreadRequest", "", "Invalid threadId or persistExtendedHistory")
        result = await self.transport.request("thread/resume", {"threadId": thread_id, "persistExtendedHistory": persist_extended_history})
        return parse_app_server_read_thread_result(result)
