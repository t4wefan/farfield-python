# Farfield Python

> **Historical protocol snapshot.** This repository faithfully ports Farfield's April 29, 2026 implementation and its Codex `0.107.0-alpha.5` schema. It is **not validated against current Codex App IPC**. A local `codex-cli 0.156.0` schema comparison on September 28, 2026 shows substantial protocol changes; see [protocol drift](docs/protocol-drift.md). Do not assume new features such as thread goals or recently added approvals are supported here.

An [experimental modern adapter](docs/modern-desktop-ipc.md) is being developed separately on the `codex/modern-desktop-ipc` branch.

Python translation of Farfield's `@farfield/api` and `@farfield/protocol` packages at upstream commit [`a479046dfa2f13b3942d9ec3e56f56a0b84e8bee`](https://github.com/achimala/farfield/commit/a479046dfa2f13b3942d9ec3e56f56a0b84e8bee). This is an independent port, not an official Farfield release. The original project and this translation retain the [MIT license](LICENSE) and Anshu Chimala's copyright notice.

The port covers desktop IPC framing, initialization and request routing, child-process Codex app-server transport, strict protocol parsing, the app-server method maps, thread snapshot/patch reduction, and the high-level follower actions. Wire field names and method strings match the TypeScript implementation. Python method names use `snake_case` (for example, `sendRequestAndWait` becomes `send_request_and_wait`). All I/O is async and can run without a graphical desktop; connecting to a Codex desktop IPC socket naturally requires the desktop app to be running.

Farfield's stable and experimental Codex app-server JSON Schema snapshot is preserved under `src/farfield_python/schemas`. `farfield_schemas.json` is a generated JSON Schema rendering of the original Zod definitions, used to validate Farfield's additional IPC/thread shapes. Generated JSON Schema cannot represent every Zod `superRefine` predicate; those rules are explicitly checked in the Python parsers. The schema snapshot identifies Codex `0.107.0-alpha.5` and should be refreshed when the upstream package changes.

```bash
uv sync
uv run pytest -q
```

```python
import asyncio
from farfield_python import DesktopIpcClient, CodexMonitorService

async def main():
    ipc = DesktopIpcClient("/tmp/codex-ipc/ipc-501.sock")
    await ipc.connect()
    try:
        await ipc.initialize("farfield/0.2.0")
        service = CodexMonitorService(ipc)
        await service.send_message(
            thread_id="thread-id", owner_client_id="desktop-client-id", text="hello"
        )
    finally:
        await ipc.disconnect()

asyncio.run(main())
```

The caller supplies the current owner client ID, as in Farfield. The high-level service does not discover ownership or keep a live-state subscription by itself. A socket integration test and a fake app-server process test exercise the wire protocol in the test suite.
