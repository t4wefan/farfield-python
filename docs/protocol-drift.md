# Protocol drift from the translated snapshot

As of September 28, 2026, Farfield's most recent `main` commit is `a479046d` (April 29, 2026). Its vendored metadata records `codex-cli 0.107.0-alpha.5`. This Python package deliberately translates that historical revision. A locally installed `codex-cli 0.156.0` exports a substantially different app-server schema.

| Method category | Farfield stable | Local CLI stable | Farfield experimental | Local CLI experimental |
| --- | ---: | ---: | ---: | ---: |
| Client requests | 42 | 101 | 52 | 164 |
| Server requests | 7 | 10 | 7 | 11 |
| Server notifications | 41 | 82 | 41 | 82 |
| v2 schema documents | 115 | 271 | 129 | 389 |

For example, `thread/goal/get`, `thread/goal/set`, `thread/goal/clear`, `thread/items/list`, and `item/permissions/requestApproval` occur in the newer schema, while the older schema includes `thread/rollback`, `skills/remote/export`, and `skills/remote/list`, which are absent in the local export. These counts compare exported app-server methods only; they say nothing conclusive about the private desktop IPC interface. The locally installed CLI may also differ from the app's bundled runtime.

Reproduce the comparison with `codex app-server generate-json-schema --out <dir>` and the additional `--experimental` flag. Compare `ClientRequest.json`, `ServerRequest.json`, `ServerNotification.json`, and `v2/` with the vendored files under `src/farfield_python/schemas/`.

For a current Codex App integration, inspect the installed app's IPC behavior and IDE client handshake directly, regenerate the app-server schemas from the exact runtime in use, then add compatibility tests with real sessions. The historical translation remains useful as a protocol reference, but it is not a compatibility guarantee.

## Newer implementation to compare

[Remodex's `phodex-bridge`](https://github.com/Emanuele-web04/remodex/tree/main/phodex-bridge), at commit `00b29057` from September 26, 2026, contains a more recent Desktop IPC follower and owner implementation. It is Apache-2.0 licensed; this document uses it as a protocol reference, not as copied code.

Its method-version table includes `thread-follower-start-turn` **2**, `thread-follower-interrupt-turn` **4**, and `thread-stream-state-changed` **11**. Farfield's follower sends version **1** for start and interrupt, making this translation unsafe to assume compatible with that newer desktop protocol. Remodex also probes ownership through `client-discovery-request` / `thread-owner-discovery`, verifies the claimed client with `thread-follower-load-complete-history`, follows with `thread-stream-following-changed`, and distinguishes timeout from proven non-delivery to avoid duplicate turns.

Remodex checks both `$CODEX_HOME/ipc/ipc.sock` and the older temporary socket, and treats a stale Unix socket file as inconclusive until an actual connection attempt. Its separate live-owner path publishes app-server-owned threads to Desktop; a follower-only SDK cannot provide that direction of synchronization.

This reference does **not** establish the exact installed Codex App's private IPC contract. Verify method versions and payloads against the target app and use the project's stricter one-second no-owner deadline before shipping an AA adapter.
