"""Desktop IPC socket client; little-endian length framing and request routing."""

from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from .errors import DesktopIpcError
from .protocol import parse_ipc_frame

MAX_FRAME_SIZE_BYTES = 256 * 1024 * 1024
INITIALIZING_CLIENT_ID = "initializing-client"


class DesktopIpcClient:
    def __init__(self, socket_path: str, request_timeout_ms: int = 20_000):
        self.socket_path = socket_path
        self.request_timeout_ms = request_timeout_ms
        self.client_id: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[str, tuple[str, asyncio.Future]] = {}
        self._frame_listeners: set[Callable[[dict], None]] = set()
        self._connection_listeners: set[Callable[[dict], None]] = set()

    def on_frame(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        self._frame_listeners.add(listener)
        return lambda: self._frame_listeners.discard(listener)

    def on_connection_state(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        self._connection_listeners.add(listener)
        return lambda: self._connection_listeners.discard(listener)

    def is_connected(self) -> bool:
        return self._writer is not None

    def get_client_id(self) -> str | None:
        return self.client_id

    def _state(self, connected: bool, reason: str | None = None) -> None:
        state = {"connected": connected}
        if reason is not None:
            state["reason"] = reason
        for listener in tuple(self._connection_listeners):
            listener(state)

    def _reject_all(self, error: Exception) -> None:
        for _, future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def connect(self) -> None:
        if self._writer is not None:
            raise DesktopIpcError("IPC client is already connected")
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(self.socket_path)
        except OSError as exc:
            raise DesktopIpcError(str(exc)) from exc
        self._reader_task = asyncio.create_task(self._read_loop())
        self._state(True)

    async def disconnect(self) -> None:
        writer = self._writer
        if writer is None:
            return
        self._writer = None
        self.client_id = None
        self._reject_all(DesktopIpcError("IPC client disconnected"))
        writer.close()
        await writer.wait_closed()
        if self._reader_task:
            await asyncio.gather(self._reader_task, return_exceptions=True)

    async def _write_frame(self, frame: dict) -> None:
        writer = self._writer
        if writer is None:
            raise DesktopIpcError("IPC socket is not connected")
        encoded = json.dumps(frame, separators=(",", ":"), ensure_ascii=False).encode("utf8")
        writer.write(struct.pack("<I", len(encoded)) + encoded)
        await writer.drain()

    async def _read_loop(self) -> None:
        try:
            while self._reader is not None:
                size = struct.unpack("<I", await self._reader.readexactly(4))[0]
                if size > MAX_FRAME_SIZE_BYTES:
                    raise DesktopIpcError(f"IPC frame exceeded limit ({size} > {MAX_FRAME_SIZE_BYTES})")
                data = await self._reader.readexactly(size)
                frame = parse_ipc_frame(json.loads(data))
                for listener in tuple(self._frame_listeners):
                    listener(frame)
                kind = frame["type"]
                if kind == "client-discovery-request":
                    await self._write_frame({"type": "client-discovery-response", "requestId": frame["requestId"], "response": {"canHandle": False}})
                elif kind == "request":
                    await self._write_frame({"type": "response", "requestId": frame["requestId"], "resultType": "error", "error": "no-handler-for-request"})
                elif kind == "response":
                    pending = self._pending.pop(frame["requestId"], None)
                    if pending is None:
                        continue
                    method, future = pending
                    if frame["resultType"] == "error":
                        error = frame.get("error")
                        message = error if isinstance(error, str) else json.dumps(error, separators=(",", ":"))
                        future.set_exception(DesktopIpcError(f"IPC {method} failed: {message}"))
                    else:
                        result = frame.get("result")
                        if frame.get("method") == "initialize" and isinstance(result, dict) and isinstance(result.get("clientId"), str):
                            self.client_id = result["clientId"]
                        future.set_result(frame)
        except (asyncio.IncompleteReadError, OSError) as exc:
            self._reject_all(DesktopIpcError("IPC socket closed"))
        except Exception as exc:
            self._reject_all(exc)
        finally:
            writer = self._writer
            self._writer = None
            self._reader = None
            self.client_id = None
            if writer is not None:
                writer.close()
            self._state(False, "IPC socket closed")

    async def send_broadcast(self, method: str, params: Any, *, target_client_id: str | None = None, version: int | None = None) -> None:
        frame = {"type": "broadcast", "method": method, "params": params, "sourceClientId": self.client_id or INITIALIZING_CLIENT_ID}
        if target_client_id is not None:
            frame["targetClientId"] = target_client_id
        if version is not None:
            frame["version"] = version
        await self._write_frame(parse_ipc_frame(frame))

    async def _request(self, frame: dict, timeout_ms: int, label: str) -> dict:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        request_id = frame["requestId"]
        self._pending[request_id] = (label, future)
        try:
            await self._write_frame(parse_ipc_frame(frame))
            return await asyncio.wait_for(future, timeout_ms / 1000)
        except asyncio.TimeoutError as exc:
            raise DesktopIpcError(f"IPC {'initialize request' if label == 'initialize' else 'request'} timed out{'' if label == 'initialize' else ': ' + label}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def send_request_and_wait(self, method: str, params: Any, *, target_client_id: str | None = None, version: int | None = None, timeout_ms: int | None = None) -> dict:
        frame = {"type": "request", "requestId": str(uuid4()), "method": method, "params": params, "sourceClientId": self.client_id or INITIALIZING_CLIENT_ID}
        if target_client_id is not None:
            frame["targetClientId"] = target_client_id
        if version is not None:
            frame["version"] = version
        return await self._request(frame, timeout_ms if timeout_ms is not None else self.request_timeout_ms, method)

    async def initialize(self, _user_agent: str) -> dict:
        return await self._request({"type": "request", "requestId": str(uuid4()), "sourceClientId": INITIALIZING_CLIENT_ID, "version": 1, "method": "initialize", "params": {"clientType": "farfield"}}, self.request_timeout_ms, "initialize")
