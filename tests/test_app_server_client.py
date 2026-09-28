import pytest

from farfield_python import AppServerClient, ProtocolValidationError


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.result = {}

    async def request(self, method, params, timeout_ms=None):
        self.calls.append((method, params))
        return self.result

    async def respond(self, request_id, result):
        self.calls.append((request_id, result))


@pytest.mark.asyncio
async def test_original_app_server_client_request_shapes():
    transport = FakeTransport()
    client = AppServerClient(transport)
    transport.result = {"thread": {"id": "thread-1", "preview": "New thread", "createdAt": 1, "updatedAt": 1, "source": "opencode"}}
    await client.start_thread(cwd="/tmp/project")
    assert transport.calls[-1] == ("thread/start", {"cwd": "/tmp/project", "ephemeral": False})
    transport.result = {"thread": {"id": "thread-1", "turns": [], "requests": []}}
    await client.resume_thread("thread-1")
    assert transport.calls[-1] == ("thread/resume", {"threadId": "thread-1", "persistExtendedHistory": True})
    await client.send_user_message("thread-1", "hello")
    assert transport.calls[-1] == ("turn/start", {"threadId": "thread-1", "input": [{"type": "text", "text": "hello"}], "attachments": []})
    await client.steer_turn(thread_id="thread-1", expected_turn_id="turn-1", input=[{"type": "text", "text": "continue"}])
    assert transport.calls[-1][0] == "turn/steer"
    await client.interrupt_turn("thread-1", "turn-2")
    assert transport.calls[-1] == ("turn/interrupt", {"threadId": "thread-1", "turnId": "turn-2"})
    await client.submit_user_input(42, {"decision": "accept"})
    assert transport.calls[-1] == (42, {"decision": "accept"})


@pytest.mark.asyncio
async def test_collaboration_mode_requires_model():
    client = AppServerClient(FakeTransport())
    with pytest.raises(ProtocolValidationError, match="collaborationMode.settings.model"):
        await client.start_turn({"threadId": "t", "input": [], "collaborationMode": {"mode": "plan", "settings": {"model": None}}})
