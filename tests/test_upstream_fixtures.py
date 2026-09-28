import json
from pathlib import Path

from farfield_python import parse_ipc_frame, parse_thread_stream_state_changed_broadcast


def test_upstream_sanitized_protocol_traces():
    files = list((Path(__file__).parent / "fixtures").glob("*.ndjson"))
    assert files
    for file in files:
        for line in file.read_text().splitlines():
            record = json.loads(line)
            if record["type"] != "history":
                continue
            payload = record["payload"]
            if payload.get("type") not in ("request", "response", "broadcast", "client-discovery-request", "client-discovery-response"):
                continue
            frame = parse_ipc_frame(payload)
            if frame["type"] == "broadcast" and frame["method"] == "thread-stream-state-changed":
                parse_thread_stream_state_changed_broadcast(frame)
