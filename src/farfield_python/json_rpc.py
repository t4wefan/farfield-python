"""JSON-RPC message validation from @farfield/api/json-rpc."""

from typing import Any

from .errors import ProtocolValidationError
from .protocol import _field, _issue, _json, _obj


def _request_id(value: Any, context: str) -> None:
    if not isinstance(value, str) and type(value) is not int:
        _issue(context, "id", "Expected string or integer")


def _base(value: Any, context: str) -> dict:
    obj = _obj(value, context)
    if "jsonrpc" in obj and obj["jsonrpc"] != "2.0":
        _issue(context, "jsonrpc", "Invalid literal value")
    if "params" in obj:
        _json(obj["params"], context)
    return obj


def parse_json_rpc_response(value: Any) -> dict:
    context = "JsonRpcResponse"
    obj = _base(value, context)
    _request_id(_field(obj, "id", "str|int", context), context)
    if "result" not in obj and "error" not in obj:
        _issue(context, "", "Response must include either result or error")
    if "result" in obj:
        _json(obj["result"], context)
    if "error" in obj:
        error = _field(obj, "error", "dict", context)
        _field(error, "code", "int", context)
        _field(error, "message", "str", context)
        if "data" in error:
            _json(error["data"], context)
    return obj


def parse_json_rpc_request(value: Any) -> dict:
    context = "JsonRpcRequest"
    obj = _base(value, context)
    _request_id(_field(obj, "id", "str|int", context), context)
    _field(obj, "method", "nonempty", context)
    return obj


def parse_json_rpc_notification(value: Any) -> dict:
    context = "JsonRpcNotification"
    obj = _base(value, context)
    _field(obj, "method", "nonempty", context)
    return obj


def parse_json_rpc_incoming_message(value: Any) -> tuple[str, dict]:
    errors = []
    for kind, parser in (("response", parse_json_rpc_response), ("request", parse_json_rpc_request), ("notification", parse_json_rpc_notification)):
        try:
            return kind, parser(value)
        except ProtocolValidationError as exc:
            errors.extend(exc.issues)
    raise ProtocolValidationError("JsonRpcIncomingMessage", errors)
