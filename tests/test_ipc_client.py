import asyncio
import json
import struct
import tempfile
import os

import pytest

from farfield_python import DesktopIpcClient, DesktopIpcError


async def read_frame(reader):
    size = struct.unpack("<I", await reader.readexactly(4))[0]
    return json.loads(await reader.readexactly(size))


async def send_frame(writer, frame):
    payload = json.dumps(frame).encode()
    raw = struct.pack("<I", len(payload)) + payload
    writer.write(raw[:3])
    await writer.drain()
    writer.write(raw[3:])
    await writer.drain()


@pytest.mark.asyncio
async def test_desktop_ipc_socket_handshake_discovery_and_error(tmp_path):
    # macOS limits AF_UNIX paths to about 104 bytes, shorter than pytest tmp paths.
    path = tempfile.mktemp(prefix="ffi-", suffix=".sock", dir="/tmp")
    received = []
    async def handler(reader, writer):
        init = await read_frame(reader)
        received.append(init)
        await send_frame(writer, {"type": "response", "requestId": init["requestId"], "method": "initialize", "resultType": "success", "result": {"clientId": "client-1"}})
        await send_frame(writer, {"type": "client-discovery-request", "requestId": "discovery", "request": {"type": "request", "requestId": "nested", "method": "ping"}})
        received.append(await read_frame(reader))
        req = await read_frame(reader)
        received.append(req)
        await send_frame(writer, {"type": "response", "requestId": req["requestId"], "resultType": "error", "error": "no-handler-for-request"})
        writer.close()
    server = await asyncio.start_unix_server(handler, path)
    async with server:
        client = DesktopIpcClient(path)
        await client.connect()
        await client.initialize("farfield/0.2.0")
        assert client.get_client_id() == "client-1"
        with pytest.raises(DesktopIpcError, match="no-handler-for-request"):
            await client.send_request_and_wait("thread-follower-start-turn", {}, target_client_id="owner", version=1)
        await client.disconnect()
    assert received[0]["params"] == {"clientType": "farfield"}
    assert received[1]["response"] == {"canHandle": False}
    assert received[2]["targetClientId"] == "owner"
    assert received[2]["sourceClientId"] == "client-1"
    os.unlink(path)
