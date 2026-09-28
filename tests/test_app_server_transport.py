import json
import sys

import pytest

from farfield_python import AppServerClient, AppServerRpcError, ChildProcessAppServerTransport


@pytest.mark.asyncio
async def test_app_server_handshake_notifications_request_and_multiline_utf8(tmp_path):
    program = tmp_path / "fake-codex"
    program.write_text(f"#!{sys.executable}\n" + '''
import json
import os
import sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        print(json.dumps({"id": message["id"], "result": {"serverInfo": {"name": "fake"}}}), flush=True)
    elif message.get("method") == "initialized":
        print(json.dumps({"method": "thread/status/changed", "params": {"threadId": "t", "status": {"type": "idle"}}}), flush=True)
        print(json.dumps({"id": "approval", "method": "item/tool/requestUserInput", "params": {"threadId": "t", "turnId": "u", "itemId": "i", "questions": []}}), flush=True)
    elif message.get("method") == "model/list":
        if message["params"]["limit"] == 2:
            sys.stdout.write('{"id":' + str(message["id"]) + ',"result":{"data":[],"preview":"hello' + chr(10) + '😀"}}\\n')
            sys.stdout.flush()
        elif message["params"]["limit"] == 3:
            print(json.dumps({"id": message["id"], "error": {"code": -32000, "message": "bad model"}}), flush=True)
        else:
            print(json.dumps({"id": message["id"], "result": {"data": []}}), flush=True)
''')
    program.chmod(0o755)
    notifications, requests = [], []
    transport = ChildProcessAppServerTransport(str(program), "farfield-python/test", experimental_api=True)
    transport.on_server_notification(notifications.append)
    transport.on_server_request(requests.append)
    client = AppServerClient(transport)
    try:
        assert await client.list_models(2) == {"data": [], "preview": "hello\n😀"}
        assert notifications[0]["method"] == "thread/status/changed"
        assert requests[0]["method"] == "item/tool/requestUserInput"
        with pytest.raises(AppServerRpcError, match="bad model"):
            await client.list_models(3)
    finally:
        await client.close()
