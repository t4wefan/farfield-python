"""Bidirectional child-process app-server transport translated from Farfield."""

from __future__ import annotations

import asyncio
import codecs
import json
import os
import sys
from collections.abc import Callable
from importlib.resources import files
from typing import Any
from uuid import uuid4

from .errors import AppServerRpcError, AppServerTransportError, ProtocolValidationError
from .json_rpc import parse_json_rpc_incoming_message, parse_json_rpc_response
from .protocol import parse_server_request


class ChildProcessAppServerTransport:
    def __init__(self, executable_path: str, user_agent: str, *, cwd: str | None = None,
                 env: dict[str, str] | None = None, request_timeout_ms: int = 30_000,
                 on_stderr: Callable[[str], None] | None = None,
                 experimental_api: bool = False,
                 opt_out_notification_methods: list[str] | None = None):
        self.executable_path = executable_path
        self.user_agent = user_agent
        self.cwd = cwd
        self.env = env or {}
        self.request_timeout_ms = request_timeout_ms
        self.on_stderr = on_stderr
        self.experimental_api = experimental_api
        self.opt_out_notification_methods = opt_out_notification_methods or []
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._notifications: set[Callable[[dict], None]] = set()
        self._requests: set[Callable[[dict], None]] = set()
        self._request_id = 0
        self._initialized = False
        self._initialize_task: asyncio.Task | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._frame_buffer = ""
        self._frame_depth = 0
        self._frame_in_string = False
        self._frame_escaped = False

    def on_server_notification(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        self._notifications.add(listener)
        return lambda: self._notifications.discard(listener)

    def on_server_request(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        self._requests.add(listener)
        return lambda: self._requests.discard(listener)

    def _reject_all(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _ensure_started(self) -> None:
        if self._process is not None:
            return
        env = {**os.environ, **self.env, "CODEX_USER_AGENT": self.user_agent, "CODEX_CLIENT_ID": f"farfield-{uuid4()}"}
        try:
            if sys.platform == "win32":
                # Node's spawn uses shell=true on Windows for .cmd executable paths.
                self._process = await asyncio.create_subprocess_shell(
                    f'"{self.executable_path}" app-server', cwd=self.cwd, env=env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
            else:
                self._process = await asyncio.create_subprocess_exec(
                    self.executable_path, "app-server", cwd=self.cwd, env=env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE)
        except OSError as exc:
            raise AppServerTransportError(f"app-server process error: {exc}") from exc
        self._stdout_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        while line := await self._process.stderr.readline():
            trimmed = line.decode("utf8", errors="replace").strip()
            if trimmed and self.on_stderr is not None:
                self.on_stderr(trimmed)

    async def _read_stdout(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        decoder = codecs.getincrementaldecoder("utf8")()
        try:
            while chunk := await process.stdout.read(65536):
                self.consume_stdout_text(decoder.decode(chunk))
            self.consume_stdout_text(decoder.decode(b"", final=True))
            await process.wait()
            reason = f"app-server exited (code={process.returncode}, signal=None)"
            self._reject_all(AppServerTransportError(reason))
        except Exception as exc:
            self._reject_all(AppServerTransportError(f"invalid app-server stdout: {exc}"))
            if process.returncode is None:
                process.terminate()
        finally:
            self._process = None
            self._initialized = False
            self._initialize_task = None
            self._frame_buffer = ""
            self._frame_depth = 0

    def consume_stdout_text(self, text: str) -> None:
        """Codex can emit unescaped control chars inside JSON strings."""
        for char in text:
            if self._frame_depth == 0:
                if char.isspace():
                    continue
                if char != "{":
                    raise AppServerTransportError(f"app-server stdout started with unexpected character: {json.dumps(char)}")
                self._frame_buffer, self._frame_depth = "{", 1
                self._frame_in_string = self._frame_escaped = False
                continue
            if self._frame_in_string:
                if self._frame_escaped:
                    self._frame_buffer += self._escape_control(char)
                    self._frame_escaped = False
                elif char == "\\":
                    self._frame_buffer += char
                    self._frame_escaped = True
                elif char == '"':
                    self._frame_buffer += char
                    self._frame_in_string = False
                else:
                    self._frame_buffer += self._escape_control(char)
                continue
            self._frame_buffer += char
            if char == '"':
                self._frame_in_string = True
            elif char == "{":
                self._frame_depth += 1
            elif char == "}":
                self._frame_depth -= 1
                if self._frame_depth == 0:
                    raw = json.loads(self._frame_buffer)
                    self._frame_buffer = ""
                    self._handle_incoming(raw)

    @staticmethod
    def _escape_control(char: str) -> str:
        return {"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}.get(char, f"\\u{ord(char):04x}" if ord(char) <= 31 else char)

    def _handle_incoming(self, raw: Any) -> None:
        kind, message = parse_json_rpc_incoming_message(raw)
        if kind == "response":
            future = self._pending.pop(message["id"], None)
            if future is None:
                return
            if "error" in message:
                error = message["error"]
                future.set_exception(AppServerRpcError(error["code"], error["message"], error.get("data")))
            else:
                future.set_result(message.get("result"))
        elif kind == "request":
            try:
                request = parse_server_request(message)
            except ProtocolValidationError:
                asyncio.create_task(self._write_payload({"id": message["id"], "error": {"code": -32600, "message": f"Unhandled app-server request: {message['method']}"}}, "error response"))
                return
            for listener in tuple(self._requests):
                listener(request)
        else:
            known = json.loads(files("farfield_python").joinpath("methods.json").read_text())["APP_SERVER_SERVER_NOTIFICATION_METHODS"]
            if message["method"] in known:
                for listener in tuple(self._notifications):
                    listener(message)

    async def _write_payload(self, payload: dict, context: str) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise AppServerTransportError("app-server failed to start")
        try:
            process.stdin.write((json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            raise AppServerTransportError(f"failed to write app-server {context}: {exc}") from exc

    async def _send_request(self, method: str, params: Any, timeout_ms: int | None) -> Any:
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        try:
            payload = {"id": request_id, "method": method}
            if params is not None:
                payload["params"] = params
            await self._write_payload(payload, "request")
            return await asyncio.wait_for(future, (timeout_ms if timeout_ms is not None else self.request_timeout_ms) / 1000)
        except asyncio.TimeoutError as exc:
            raise AppServerTransportError(f"app-server request timed out: {method}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        if self._initialize_task is not None:
            await self._initialize_task
            return

        async def initialize() -> None:
            capabilities: dict = {}
            if self.experimental_api:
                capabilities["experimentalApi"] = True
            if self.opt_out_notification_methods:
                capabilities["optOutNotificationMethods"] = list(self.opt_out_notification_methods)
            params = {"clientInfo": {"name": "farfield", "version": "0.2.0"}}
            if capabilities:
                params["capabilities"] = capabilities
            await self._send_request("initialize", params, self.request_timeout_ms)
            await self._write_payload({"method": "initialized"}, "notification")
            self._initialized = True

        self._initialize_task = asyncio.create_task(initialize())
        try:
            await self._initialize_task
        finally:
            self._initialize_task = None

    async def request(self, method: str, params: Any, timeout_ms: int | None = None) -> Any:
        await self._ensure_started()
        if method != "initialize":
            await self._ensure_initialized()
        result = await self._send_request(method, params, timeout_ms)
        if method == "initialize":
            self._initialized = True
        return result

    async def respond(self, request_id: str | int, result: Any) -> None:
        await self._ensure_started()
        await self._ensure_initialized()
        payload = parse_json_rpc_response({"id": request_id, "result": result})
        await self._write_payload(payload, "response")

    async def close(self) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        self._initialized = False
        self._initialize_task = None
        self._reject_all(AppServerTransportError("app-server transport closed"))
        process.terminate()
        await process.wait()
        for task in (self._stdout_task, self._stderr_task):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
