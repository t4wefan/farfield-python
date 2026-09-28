import pytest

from farfield_python import (
    CODEX_CLIENT_REQUEST_METHOD_MAP, APP_SERVER_CLIENT_REQUEST_METHODS,
    CODEX_SERVER_NOTIFICATION_METHOD_MAP, APP_SERVER_SERVER_NOTIFICATION_METHODS,
    ProtocolValidationError, apply_strict_patch, find_latest_turn_params_template,
    parse_command_execution_request_approval_response, parse_ipc_frame,
    parse_thread_conversation_state, parse_thread_stream_patch,
    parse_user_input_response_payload, reduce_thread_stream_events,
)


def test_method_manifest_exactly_covers_maps():
    assert set(APP_SERVER_CLIENT_REQUEST_METHODS) == set(CODEX_CLIENT_REQUEST_METHOD_MAP)
    assert set(APP_SERVER_SERVER_NOTIFICATION_METHODS) == set(CODEX_SERVER_NOTIFICATION_METHOD_MAP)
    assert CODEX_CLIENT_REQUEST_METHOD_MAP["turn/start"] == {"status": "exposed", "commandKind": "sendMessage"}


def test_ipc_passthrough_and_invalid_fields():
    frame = {"type": "request", "requestId": "one", "method": "initialize", "custom": 3}
    assert parse_ipc_frame(frame) == frame
    with pytest.raises(ProtocolValidationError):
        parse_ipc_frame({**frame, "version": -1})
    with pytest.raises(ProtocolValidationError):
        parse_ipc_frame({**frame, "requestId": ""})


def test_strict_patch_and_reducer():
    state = {"id": "thread-1", "turns": [{"status": "completed", "items": [], "params": {"threadId": "thread-1", "input": [{"type": "text", "text": "old"}]}}]}
    assert parse_thread_conversation_state(state)["requests"] == []
    assert find_latest_turn_params_template(state)["input"][0]["text"] == "old"
    updated = apply_strict_patch(state, {"op": "add", "path": ["turns", 0, "items", 0], "value": {"id": "i", "type": "agentMessage", "text": "hello"}})
    assert updated["turns"][0]["items"][0]["text"] == "hello"
    assert state["turns"][0]["items"] == []
    def event(change, owner="owner-1"):
        return {"type": "broadcast", "method": "thread-stream-state-changed", "sourceClientId": owner,
                "version": 1, "params": {"type": "thread-stream-state-changed", "conversationId": "thread-1", "version": 1, "change": change}}
    result = reduce_thread_stream_events([event({"type": "snapshot", "conversationState": state}), event({"type": "patches", "patches": [{"op": "add", "path": ["turns", 0, "items", 0], "value": {"id": "i", "type": "agentMessage", "text": "hello"}}]}, "owner-2")])
    assert result["thread-1"] == {"ownerClientId": "owner-2", "conversationState": updated}
    with pytest.raises(ProtocolValidationError):
        parse_thread_stream_patch({"op": "remove", "path": ["turns"], "value": None})
    with pytest.raises(ValueError, match="patch event arrived before snapshot"):
        reduce_thread_stream_events([event({"type": "patches", "patches": []})])


def test_approval_decision_strictness():
    payload = {"decision": {"applyNetworkPolicyAmendment": {"network_policy_amendment": {"action": "allow", "host": "example.com"}}}}
    assert parse_command_execution_request_approval_response(payload) == payload
    with pytest.raises(ProtocolValidationError):
        parse_command_execution_request_approval_response({"decision": "accept", "unexpected": True})
    assert parse_user_input_response_payload({"decision": "approved_for_session"}) == {"decision": "approved_for_session"}


def test_default_user_content_matches_upstream_zod():
    parsed = parse_thread_conversation_state({"id": "thread", "turns": [{"status": "completed", "items": [{"type": "userMessage", "id": "item"}]}]})
    assert parsed["turns"][0]["items"][0]["content"] == []
