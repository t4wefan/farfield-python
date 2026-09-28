import pytest

from farfield_python import CodexMonitorService, ProtocolValidationError


class FakeIpc:
    def __init__(self):
        self.calls = []

    async def send_request_and_wait(self, method, params, **options):
        self.calls.append((method, params, options))
        return {"type": "response", "resultType": "success", "requestId": "one"}


@pytest.mark.asyncio
async def test_follower_actions_and_template_override():
    ipc = FakeIpc()
    service = CodexMonitorService(ipc)
    await service.send_message(thread_id="thread", owner_client_id="owner", text=" new ", turn_start_template={"threadId": "other", "cwd": "/tmp", "input": [], "attachments": []}, model="gpt", is_steering=True)
    method, params, options = ipc.calls[0]
    assert method == "thread-follower-start-turn"
    assert params["turnStartParams"] == {"threadId": "thread", "cwd": "/tmp", "input": [{"type": "text", "text": "new"}], "attachments": [], "model": "gpt"}
    assert params["isSteering"] is True
    assert options == {"target_client_id": "owner", "version": 1}
    await service.submit_user_input(thread_id="thread", owner_client_id="owner", request_id=7, response={"answers": {"q": {"answers": ["A"]}}})
    await service.submit_command_approval_decision(thread_id="thread", owner_client_id="owner", request_id=8, response={"decision": "acceptForSession"})
    await service.submit_file_approval_decision(thread_id="thread", owner_client_id="owner", request_id=9, response={"decision": "decline"})
    await service.set_collaboration_mode(thread_id="thread", owner_client_id="owner", collaboration_mode={"mode": "plan", "settings": {}})
    await service.interrupt(thread_id="thread", owner_client_id="owner")
    assert [call[0] for call in ipc.calls[1:]] == ["thread-follower-submit-user-input", "thread-follower-command-approval-decision", "thread-follower-file-approval-decision", "thread-follower-set-collaboration-mode", "thread-follower-interrupt-turn"]
    with pytest.raises(ProtocolValidationError):
        await service.submit_file_approval_decision(thread_id="thread", owner_client_id="owner", request_id=9, response={"decision": "wrong"})
