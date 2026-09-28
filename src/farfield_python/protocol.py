"""Python translation of @farfield/protocol's public parsing helpers.

Known fields retain Zod's exact types; unknown fields are preserved where the
original schema uses ``passthrough``. The upstream Codex generated schemas are
vendored as their original JSON Schema documents under ``schemas/``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft7Validator

from .errors import ProtocolValidationError


def _issue(context: str, path: str, message: str) -> None:
    raise ProtocolValidationError(context, [f"{path or '<root>'}: {message}"])


def _obj(value: Any, context: str, path: str = "") -> dict:
    if not isinstance(value, dict):
        _issue(context, path, "Expected object")
    return value


def _field(value: dict, key: str, kind: str, context: str, *, required: bool = True) -> Any:
    if key not in value:
        if required:
            _issue(context, key, "Required")
        return None
    item = value[key]
    kinds = kind.split("|")
    if not any(
        (k == "str" and isinstance(item, str))
        or (k == "nonempty" and isinstance(item, str) and len(item) > 0)
        or (k == "bool" and type(item) is bool)
        or (k == "int" and type(item) is int)
        or (k == "uint" and type(item) is int and item >= 0)
        or (k == "list" and isinstance(item, list))
        or (k == "dict" and isinstance(item, dict))
        or (k == "null" and item is None)
        for k in kinds
    ):
        _issue(context, key, f"Expected {kind}")
    return item


def _json(value: Any, context: str = "JsonValue") -> Any:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        _issue(context, "", str(exc))
    return value


@lru_cache(maxsize=64)
def _schema(channel: str, name: str) -> dict:
    return json.loads(files("farfield_python").joinpath("schemas", channel, f"{name}.json").read_text())


def parse_generated(value: Any, name: str, *, channel: str = "stable", context: str | None = None) -> Any:
    """Validate with the unmodified Codex app-server JSON Schema snapshot."""
    schema = _schema(channel, name)
    error = next(Draft7Validator(schema).iter_errors(value), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path)
        _issue(context or name, path, error.message)
    return value


@lru_cache(maxsize=1)
def _farfield_schemas() -> dict:
    return json.loads(files("farfield_python").joinpath("farfield_schemas.json").read_text())


def parse_farfield_schema(value: Any, name: str, *, context: str | None = None) -> Any:
    """Validate against the Python JSON Schema rendering of Farfield's Zod schema.

    JSON Schema cannot express Zod ``superRefine``; those constraints are
    checked by the specialized parse helpers below.
    """
    schema = _farfield_schemas()[name]
    value = _apply_defaults(value, schema, schema)
    error = next(Draft7Validator(schema).iter_errors(value), None)
    if error:
        path = ".".join(str(p) for p in error.absolute_path)
        _issue(context or name.removesuffix("Schema"), path, error.message)
    return value


def _apply_defaults(value: Any, shape: dict, root: dict) -> Any:
    if "$ref" in shape:
        target = root
        for part in shape["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return _apply_defaults(value, target, root)
    for branch in shape.get("anyOf", []):
        if Draft7Validator({"definitions": root.get("definitions", {}), **branch}).is_valid(value):
            return _apply_defaults(value, branch, root)
    if isinstance(value, dict) and shape.get("type") == "object":
        result = value.copy()
        for field, subshape in shape.get("properties", {}).items():
            if field not in result and "default" in subshape:
                result[field] = subshape["default"]
            if field in result:
                result[field] = _apply_defaults(result[field], subshape, root)
        return result
    if isinstance(value, list) and shape.get("type") == "array":
        return [_apply_defaults(item, shape["items"], root) for item in value]
    return value


def parse_ipc_frame(value: Any) -> dict:
    value = parse_farfield_schema(value, "IpcFrameSchema", context="IpcFrame")
    context = "IpcFrame"
    frame = _obj(value, context)
    kind = _field(frame, "type", "str", context)
    if kind not in {"request", "response", "broadcast", "client-discovery-request", "client-discovery-response"}:
        _issue(context, "type", "Invalid discriminator value")
    if kind in {"request", "response", "client-discovery-request", "client-discovery-response"}:
        _field(frame, "requestId", "nonempty", context)
    if kind in {"request", "broadcast"}:
        _field(frame, "method", "nonempty", context)
        for key in ("sourceClientId", "targetClientId"):
            _field(frame, key, "nonempty", context, required=False)
        _field(frame, "version", "uint", context, required=False)
    if kind == "response":
        _field(frame, "method", "nonempty", context, required=False)
        _field(frame, "handledByClientId", "nonempty", context, required=False)
        if _field(frame, "resultType", "str", context) not in {"success", "error"}:
            _issue(context, "resultType", "Invalid enum value")
    if kind == "client-discovery-request":
        request = _field(frame, "request", "dict", context)
        if parse_ipc_frame(request)["type"] != "request":
            _issue(context, "request", "Expected request frame")
    if kind == "client-discovery-response":
        _field(_field(frame, "response", "dict", context), "canHandle", "bool", context)
    return frame


def parse_collaboration_mode(value: Any) -> dict:
    mode = _obj(value, "CollaborationMode")
    _field(mode, "mode", "nonempty", "CollaborationMode")
    settings = _field(mode, "settings", "dict", "CollaborationMode")
    for key in ("model", "reasoning_effort", "developer_instructions"):
        _field(settings, key, "str|null", "CollaborationMode", required=False)
    return mode


def parse_turn_start_params(value: Any) -> dict:
    value = parse_farfield_schema(value, "TurnStartParamsSchema", context="TurnStartParams")
    context = "TurnStartParams"
    params = _obj(value, context)
    _field(params, "threadId", "nonempty", context)
    for part in _field(params, "input", "list", context):
        item = _obj(part, context)
        if item.get("type") == "text":
            _field(item, "text", "str", context)
            elements = _field(item, "text_elements", "list", context, required=False)
            if elements is not None:
                for element in elements:
                    _json(element, context)
        elif item.get("type") == "image":
            _field(item, "url", "str", context)
        else:
            _issue(context, "input.type", "Invalid discriminator value")
    for key in ("cwd", "approvalPolicy", "summary"):
        _field(params, key, "str" if key == "summary" else "nonempty", context, required=False)
    for key in ("model", "effort"):
        _field(params, key, "str|null", context, required=False)
    if "sandboxPolicy" in params:
        _field(_field(params, "sandboxPolicy", "dict", context), "type", "nonempty", context)
    if "attachments" in params:
        for item in _field(params, "attachments", "list", context):
            _json(item, context)
    if params.get("collaborationMode") is not None:
        parse_collaboration_mode(params["collaborationMode"])
    for key in ("personality", "outputSchema"):
        if key in params:
            _json(params[key], context)
    return params


def parse_thread_conversation_state(value: Any) -> dict:
    value = parse_farfield_schema(value, "ThreadConversationStateSchema", context="ThreadConversationState")
    context = "ThreadConversationState"
    state = _obj(value, context).copy()
    _field(state, "id", "nonempty", context)
    for turn in _field(state, "turns", "list", context):
        t = _obj(turn, context)
        _field(t, "status", "nonempty", context)
        for item in _field(t, "items", "list", context):
            i = _obj(item, context)
            kind = _field(i, "type", "str", context)
            if kind not in TURN_ITEM_TYPES:
                _issue(context, "turns.items.type", "Invalid discriminator value")
            for field, field_type in TURN_ITEM_TYPES[kind].items():
                _field(i, field, field_type, context)
        if "params" in t:
            parse_turn_start_params(t["params"])
        for key in ("turnId",):
            _field(t, key, "nonempty|null", context, required=False)
        _field(t, "id", "nonempty", context, required=False)
        for key in ("turnStartedAtMs", "finalAssistantStartedAtMs"):
            _field(t, key, "uint|null", context, required=False)
    if "requests" not in state:
        state["requests"] = []
    for request in _field(state, "requests", "list", context):
        r = _obj(request, context)
        _field(r, "id", "str|int", context)
        method = _field(r, "method", "nonempty", context)
        if method == "item/plan/requestImplementation":
            p = _field(r, "params", "dict", context)
            for key in ("threadId", "turnId"):
                _field(p, key, "nonempty", context)
            _field(p, "planContent", "str", context)
        else:
            parse_server_request(r)
        _field(r, "completed", "bool", context, required=False)
    for key in ("createdAt", "updatedAt"):
        _field(state, key, "uint", context, required=False)
    for key in ("title", "latestModel", "latestReasoningEffort", "previousTurnModel"):
        _field(state, key, "str|null", context, required=False)
    for key in ("hasUnreadTurn",):
        _field(state, key, "bool", context, required=False)
    for key in ("rolloutPath", "cwd", "resumeState", "source"):
        _field(state, key, "str", context, required=False)
    if state.get("latestCollaborationMode") is not None:
        parse_collaboration_mode(state["latestCollaborationMode"])
    if "status" in state:
        status = _field(state, "status", "dict", context)
        if status.get("type") not in {"idle", "active", "notLoaded", "systemError"}:
            _issue(context, "status.type", "Invalid discriminator value")
        if status["type"] == "active":
            flags = _field(status, "activeFlags", "list", context)
            if any(f not in {"waitingOnApproval", "waitingOnUserInput"} for f in flags):
                _issue(context, "status.activeFlags", "Invalid enum value")
    return state


# Each entry is a discriminator from the original TurnItemSchema. Known required
# fields are validated here; optional fields are validated in the extended map.
TURN_ITEM_TYPES: dict[str, dict[str, str]] = {
    "userMessage": {"id": "nonempty", "content": "list"},
    "steeringUserMessage": {"id": "nonempty", "content": "list"},
    "agentMessage": {"id": "nonempty", "text": "str"},
    "error": {"id": "nonempty", "message": "str"},
    "reasoning": {"id": "nonempty"},
    "plan": {"id": "nonempty", "text": "str"},
    "todo-list": {"id": "nonempty", "plan": "list"},
    "planImplementation": {"id": "nonempty", "turnId": "nonempty", "planContent": "str"},
    "userInputResponse": {"id": "nonempty", "requestId": "str|uint", "turnId": "nonempty", "questions": "list", "answers": "dict"},
    "commandExecution": {"id": "nonempty", "command": "str", "status": "nonempty"},
    "fileChange": {"id": "nonempty", "changes": "list", "status": "nonempty"},
    "contextCompaction": {"id": "nonempty"},
    "webSearch": {"id": "nonempty", "query": "str"},
    "message": {"role": "str", "content": "list"},
    "local_shell_call": {"call_id": "nonempty|null", "status": "str", "action": "dict"},
    "web_search_call": {"status": "str", "action": "dict"},
    "mcpToolCall": {"id": "nonempty", "server": "str", "tool": "str", "status": "str"},
    "dynamicToolCall": {"id": "nonempty", "tool": "str", "status": "str"},
    "custom_tool_call": {"call_id": "nonempty", "name": "str", "input": "str", "status": "str"},
    "custom_tool_call_output": {"call_id": "nonempty"},
    "function_call": {"call_id": "nonempty", "name": "str", "arguments": "str"},
    "function_call_output": {"call_id": "nonempty"},
    "tool_search_call": {"call_id": "nonempty", "status": "str", "execution": "str"},
    "tool_search_output": {"call_id": "nonempty", "status": "str", "execution": "str", "tools": "list"},
    "ghost_snapshot": {}, "compaction": {"encrypted_content": "str"}, "other": {},
    "automaticApprovalReview": {"id": "nonempty", "status": "nonempty"},
    "mcpServerElicitation": {"id": "nonempty", "requestId": "str|uint", "turnId": "nonempty"},
    "collabAgentToolCall": {"id": "nonempty", "tool": "str", "status": "str", "senderThreadId": "str", "receiverThreadIds": "list", "agentsStates": "dict"},
    "imageView": {"id": "nonempty", "path": "str"},
    "enteredReviewMode": {"id": "nonempty", "review": "str"},
    "exitedReviewMode": {"id": "nonempty", "review": "str"},
    "remoteTaskCreated": {"id": "nonempty", "taskId": "nonempty"},
    "modelChanged": {"id": "nonempty"},
    "forkedFromConversation": {"id": "nonempty", "sourceConversationId": "nonempty"},
    "steered": {"id": "nonempty"},
}


def parse_thread_stream_patch(value: Any) -> dict:
    value = parse_farfield_schema(value, "ThreadStreamPatchSchema", context="ThreadStreamPatch")
    patch = _obj(value, "ThreadStreamPatch")
    if patch.get("op") not in {"add", "replace", "remove"}:
        _issue("ThreadStreamPatch", "op", "Invalid enum value")
    path = _field(patch, "path", "list", "ThreadStreamPatch")
    if not path or any(not (type(p) is int and p >= 0 or isinstance(p, str) and p) for p in path):
        _issue("ThreadStreamPatch", "path", "Expected nonempty path of nonnegative integers or nonempty strings")
    if (patch["op"] == "remove") == ("value" in patch):
        _issue("ThreadStreamPatch", "", "remove patches must not include value" if patch["op"] == "remove" else f"{patch['op']} patches must include value")
    if "value" in patch:
        _json(patch["value"], "ThreadStreamPatch")
    return patch


def parse_thread_stream_state_changed_params(value: Any) -> dict:
    value = parse_farfield_schema(value, "ThreadStreamStateChangedParamsSchema", context="ThreadStreamStateChangedParams")
    context = "ThreadStreamStateChangedParams"
    params = _obj(value, context)
    if _field(params, "type", "str", context) != "thread-stream-state-changed":
        _issue(context, "type", "Invalid discriminator value")
    _field(params, "conversationId", "nonempty", context)
    _field(params, "version", "uint", context)
    change = _field(params, "change", "dict", context)
    if change.get("type") == "snapshot":
        copied = params.copy()
        copied["change"] = change.copy()
        copied["change"]["conversationState"] = parse_thread_conversation_state(change.get("conversationState"))
        return copied
    if change.get("type") == "patches":
        for patch in _field(change, "patches", "list", context):
            parse_thread_stream_patch(patch)
        return params
    _issue(context, "change.type", "Invalid discriminator value")


def parse_thread_stream_state_changed_broadcast(value: Any) -> dict:
    context = "ThreadStreamStateChangedBroadcast"
    frame = parse_ipc_frame(value)
    if frame["type"] != "broadcast" or frame["method"] != "thread-stream-state-changed":
        _issue(context, "method", "Invalid discriminator value")
    _field(frame, "sourceClientId", "nonempty", context)
    _field(frame, "version", "uint", context)
    frame = frame.copy()
    frame["params"] = parse_thread_stream_state_changed_params(frame.get("params"))
    return frame


def parse_server_request(value: Any) -> dict:
    errors = []
    for channel in ("stable", "experimental"):
        try:
            return parse_generated(value, "ServerRequest", channel=channel)
        except ProtocolValidationError as exc:
            errors.extend(exc.issues)
    raise ProtocolValidationError("AppServerServerRequest", errors)


def parse_command_execution_request_approval_response(value: Any) -> dict:
    value = parse_farfield_schema(value, "CommandExecutionRequestApprovalResponseSchema", context="CommandExecutionRequestApprovalResponse")
    context = "CommandExecutionRequestApprovalResponse"
    obj = _obj(value, context)
    if set(obj) != {"decision"}:
        _issue(context, "", "Unrecognized key or missing decision")
    decision = obj["decision"]
    if decision in ("accept", "acceptForSession", "decline", "cancel") if isinstance(decision, str) else False:
        return obj
    if isinstance(decision, dict) and len(decision) == 1:
        if "acceptWithExecpolicyAmendment" in decision:
            nested = decision["acceptWithExecpolicyAmendment"]
            if isinstance(nested, dict) and set(nested) == {"execpolicy_amendment"} and isinstance(nested["execpolicy_amendment"], list) and all(isinstance(x, str) for x in nested["execpolicy_amendment"]):
                return obj
        if "applyNetworkPolicyAmendment" in decision:
            nested = decision["applyNetworkPolicyAmendment"]
            if isinstance(nested, dict) and set(nested) == {"network_policy_amendment"}:
                rule = nested["network_policy_amendment"]
                if isinstance(rule, dict) and set(rule) == {"action", "host"} and rule["action"] in ("allow", "deny") and isinstance(rule["host"], str):
                    return obj
    _issue(context, "decision", "Invalid approval decision")


def parse_file_change_request_approval_response(value: Any) -> dict:
    value = parse_farfield_schema(value, "FileChangeRequestApprovalResponseSchema", context="FileChangeRequestApprovalResponse")
    obj = _obj(value, "FileChangeRequestApprovalResponse")
    if set(obj) != {"decision"} or not isinstance(obj["decision"], str) or obj["decision"] not in ("accept", "acceptForSession", "decline", "cancel"):
        _issue("FileChangeRequestApprovalResponse", "decision", "Invalid approval decision")
    return obj


def parse_tool_request_user_input_response_payload(value: Any) -> dict:
    return parse_farfield_schema(value, "ToolRequestUserInputResponsePayloadSchema", context="ToolRequestUserInputResponsePayload")


def parse_user_input_response_payload(value: Any) -> dict:
    for parser in (parse_tool_request_user_input_response_payload, parse_command_execution_request_approval_response, parse_file_change_request_approval_response,
                   lambda item: parse_farfield_schema(item, "LegacyReviewApprovalResponseSchema", context="LegacyReviewApprovalResponse")):
        try:
            return parser(value)
        except ProtocolValidationError:
            continue
    _issue("UserInputResponsePayload", "", "Invalid response payload")


def parse_app_server_list_threads_response(value: Any) -> dict:
    value = parse_farfield_schema(value, "AppServerListThreadsResponseSchema", context="AppServerListThreadsResponse")
    obj = _obj(value, "AppServerListThreadsResponse")
    items = _field(obj, "data", "list", "AppServerListThreadsResponse")
    for item in items:
        if isinstance(item, dict) and item.get("source") == "opencode":
            for key, kind in {"id": "nonempty", "preview": "str", "createdAt": "uint", "updatedAt": "uint"}.items():
                _field(item, key, kind, "AppServerListThreadsResponse")
        else:
            parse_generated({"data": [item]}, "v2/ThreadListResponse", context="AppServerListThreadsResponse")
    _field(obj, "nextCursor", "str|null", "AppServerListThreadsResponse", required=False)
    _field(obj, "pages", "uint", "AppServerListThreadsResponse", required=False)
    _field(obj, "truncated", "bool", "AppServerListThreadsResponse", required=False)
    return obj


def parse_app_server_read_thread_response(value: Any) -> dict:
    # The exported protocol helper validates the generated Codex shape first.
    parse_generated(value, "v2/ThreadReadResponse", context="GeneratedAppServerReadThreadResponse")
    return parse_app_server_read_thread_result(value)


def parse_app_server_read_thread_result(value: Any) -> dict:
    """Subset validator used by AppServerClient, matching its Zod schema."""
    obj = _obj(value, "AppServerReadThreadResponse")
    obj = obj.copy()
    obj["thread"] = parse_thread_conversation_state(obj.get("thread"))
    return obj


def parse_app_server_list_models_response(value: Any) -> dict:
    return parse_generated(value, "v2/ModelListResponse", context="AppServerListModelsResponse")


def parse_app_server_collaboration_mode_list_response(value: Any) -> dict:
    return parse_generated(value, "v2/CollaborationModeListResponse", channel="experimental", context="AppServerCollaborationModeListResponse")


def parse_app_server_start_thread_response(value: Any) -> dict:
    value = parse_farfield_schema(value, "AppServerStartThreadResponseSchema", context="AppServerStartThreadResponse")
    obj = _obj(value, "AppServerStartThreadResponse")
    parse_app_server_list_threads_response({"data": [obj.get("thread")]})
    return obj


def parse_app_server_get_account_rate_limits_response(value: Any) -> dict:
    return parse_generated(value, "v2/GetAccountRateLimitsResponse", context="AppServerGetAccountRateLimitsResponse")
