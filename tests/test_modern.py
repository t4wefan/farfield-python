import asyncio
import json
import os
import struct
import tempfile

import pytest

from farfield_python import DesktopIpcError, ModernCodexFollower, ModernDesktopIpcClient, ipc_socket_candidates, parse_modern_ipc_frame


def test_current_router_discovery_envelope():
    frame = {"type": "client-discovery-request", "requestId": "outer", "request": {
        "type": "request", "sourceClientId": "other", "method": "thread-owner-discovery",
        "version": 1, "params": {"conversationId": "t", "hostId": "local"}}}
    assert parse_modern_ipc_frame(frame) is frame
    with pytest.raises(Exception):
        parse_modern_ipc_frame({**frame, "requestId": None})


async def read_frame(reader):
    length = struct.unpack("<I", await reader.readexactly(4))[0]
    return json.loads(await reader.readexactly(length))


async def write_frame(writer, frame):
    payload = json.dumps(frame).encode()
    writer.write(struct.pack("<I", len(payload)) + payload)
    await writer.drain()


@pytest.mark.asyncio
async def test_modern_versions_owner_verification_and_turn(tmp_path):
    path = tempfile.mktemp(prefix="aa-modern-", suffix=".sock", dir="/tmp")
    messages = []
    async def handler(reader, writer):
        try:
            while True:
                frame = await read_frame(reader)
                messages.append(frame)
                if frame["type"] == "broadcast":
                    continue
                response = {"type": "response", "requestId": frame["requestId"], "method": frame["method"], "resultType": "success"}
                if frame["method"] == "initialize":
                    response["result"] = {"clientId": "aa-1"}
                elif frame["method"] == "thread-owner-discovery":
                    response["handledByClientId"] = "desktop-owner"
                    response["result"] = {"supportsUntrustedAppInput": False}
                elif frame["method"] == "thread-follower-load-complete-history":
                    response["result"] = {"revision": 4}
                else:
                    response["result"] = {"turn": {"id": "turn-1"}}
                await write_frame(writer, response)
        except asyncio.IncompleteReadError:
            writer.close()
    server = await asyncio.start_unix_server(handler, path)
    async with server:
        client = ModernDesktopIpcClient([path])
        await client.connect()
        try:
            await client.initialize("aa/test")
            await client.follow("thread-1")
            follower = ModernCodexFollower(client)
            await follower.send_message(thread_id="thread-1", text="hello", model="gpt-test")
            await follower.interrupt(thread_id="thread-1", turn_id="turn-1")
            await follower.steer(thread_id="thread-1", turn_id="turn-1", text="continue", cwd="/tmp/work")
            await follower.compact(thread_id="thread-1")
            assert [m["method"] for m in messages if m["type"] == "request"] == [
                "initialize", "thread-owner-discovery", "thread-follower-load-complete-history",
                "thread-follower-update-thread-settings", "thread-follower-start-turn",
                "thread-owner-discovery", "thread-follower-load-complete-history", "thread-follower-interrupt-turn",
                "thread-owner-discovery", "thread-follower-load-complete-history", "thread-follower-steer-turn",
                "thread-owner-discovery", "thread-follower-load-complete-history", "thread-follower-compact-thread",
            ]
            assert messages[0]["params"] == {"clientType": "aa-bridge"}
            start = next(m for m in messages if m.get("method") == "thread-follower-start-turn")
            assert start["version"] == 2
            assert start["targetClientId"] == "desktop-owner"
            assert start["params"]["turnStart"]["context"] == {"inheritThreadSettings": True}
            interrupt = next(m for m in messages if m.get("method") == "thread-follower-interrupt-turn")
            assert interrupt["version"] == 4
            assert interrupt["params"] == {"conversationId": "thread-1", "mode": "user-stop", "expectedTurnId": "turn-1"}
            steer = next(m for m in messages if m.get("method") == "thread-follower-steer-turn")
            assert steer["params"]["restoreMessage"]["context"]["workspaceRoots"] == ["/tmp/work"]
            assert steer["params"]["expectedTurnId"] == "turn-1"
        finally:
            await client.disconnect()
    os.unlink(path)


@pytest.mark.asyncio
async def test_no_owner_stops_within_configured_deadline():
    path = tempfile.mktemp(prefix="aa-absent-", suffix=".sock", dir="/tmp")
    messages = []
    async def handler(reader, writer):
        try:
            while True:
                frame = await read_frame(reader)
                messages.append(frame)
                if frame["method"] == "initialize":
                    await write_frame(writer, {"type": "response", "requestId": frame["requestId"], "method": "initialize", "resultType": "success", "result": {"clientId": "aa-1"}})
        except asyncio.IncompleteReadError:
            writer.close()
    server = await asyncio.start_unix_server(handler, path)
    async with server:
        client = ModernDesktopIpcClient([path])
        await client.connect()
        await client.initialize("aa/test")
        started = asyncio.get_running_loop().time()
        assert await client.discover_owner("absent", no_owner_timeout_ms=50) is None
        assert asyncio.get_running_loop().time() - started < 0.2
        with pytest.raises(DesktopIpcError, match="No Codex Desktop owner"):
            await ModernCodexFollower(client).send_message(thread_id="absent", text="do work")
        assert not any(m.get("method") == "thread-follower-start-turn" for m in messages)
        await client.disconnect()
    os.unlink(path)


def test_current_then_legacy_socket_paths():
    paths = ipc_socket_candidates(codex_home="/tmp/example-codex-home")
    assert paths[0] == "/tmp/example-codex-home/ipc/ipc.sock"
    assert paths[1].endswith(f"codex-ipc/ipc-{os.getuid()}.sock")


@pytest.mark.asyncio
async def test_stale_owner_claim_does_not_extend_no_owner_deadline():
    path = tempfile.mktemp(prefix="aa-stale-", suffix=".sock", dir="/tmp")
    async def handler(reader, writer):
        try:
            while True:
                frame = await read_frame(reader)
                response = {"type": "response", "requestId": frame["requestId"], "method": frame["method"], "resultType": "success"}
                if frame["method"] == "initialize":
                    response["result"] = {"clientId": "aa-1"}
                elif frame["method"] == "thread-owner-discovery":
                    response["handledByClientId"] = "stale-owner"
                    response["result"] = {}
                else:
                    continue  # A stale route claims ownership but never answers history.
                await write_frame(writer, response)
        except asyncio.IncompleteReadError:
            writer.close()
    server = await asyncio.start_unix_server(handler, path)
    async with server:
        client = ModernDesktopIpcClient([path])
        await client.connect()
        await client.initialize("aa/test")
        started = asyncio.get_running_loop().time()
        with pytest.raises(DesktopIpcError, match="Could not verify"):
            await ModernCodexFollower(client, owner_probe_timeout_ms=100).send_message(thread_id="stale", text="work")
        assert asyncio.get_running_loop().time() - started < 0.25
        await client.disconnect()
    os.unlink(path)
