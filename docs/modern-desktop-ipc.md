# Experimental modern Desktop IPC adapter

The `codex/modern-desktop-ipc` branch adds an opt-in Python adapter in `farfield_python.modern`. It keeps the historical Farfield translation unchanged on `main`. The current method versions and follower payload shapes were checked against [Remodex `phodex-bridge`](https://github.com/Emanuele-web04/remodex/tree/00b29057c35794d7802b3fbc7d9a4d102d34517f/phodex-bridge) (Apache-2.0), not copied from its source. The specific installed Codex App is the final authority.

```python
from farfield_python import ModernDesktopIpcClient, ModernCodexFollower

ipc = ModernDesktopIpcClient()  # CODEX_HOME/ipc/ipc.sock, then legacy temp socket
await ipc.connect()
await ipc.initialize("aa/0.1")
try:
    follower = ModernCodexFollower(ipc)
    await ipc.follow("thread-id")
    await follower.send_message(thread_id="thread-id", text="hello")
finally:
    await ipc.disconnect()
```

Before a mutation, the adapter asks the IPC bus for a thread owner and verifies that owner with the read-only `thread-follower-load-complete-history` method. An absent or stale owner is rejected in an 800 ms probe budget. It never silently replays a timed-out write through another app-server, because the first owner may already be running the turn. `start-turn` uses method version 2 and the current `turnStart.request` envelope; `interrupt` uses version 4 and `mode: "user-stop"`. It also implements follow/unfollow, steering, compacting, and approval responses.

On September 28, 2026, a direct connection to the locally running Codex App IPC accepted the `aa-bridge` initialize handshake. A probe of a random nonexistent thread reported no owner in about 801 ms. The automated suite exercises fake IPC owners, timeouts, requests and method versions. **No actual Desktop-owned thread was mutated by these tests.** App-owned stream publication, complete current snapshot parsing, goal and plan projection, and AA user interface integration remain separate work.
