# ADR-0003: Spawn Performance Findings on macOS

## Status: Observation (informational)

## Context

After implementing spawn-based multiprocessing on macOS (ADR-0002),
CI tests revealed significant performance differences compared to
Linux (fork). This document records profiling findings and known
issues for future development.

## Profiling Results (local macOS ARM64)

### Worker spawn overhead
- Single spawn worker lifecycle: ~0.46s (imports + setup)
  - `SQLServer` import: 0.22s
  - `server` module import: 0.15s
  - Other imports: 0.01s
  - Process overhead: 0.07s
- With 2 API + 2 task workers: server ready in **1.65s**
- With 12 API + 12 task workers: server ready in **~4s** locally

### Comparison: fork vs spawn server start
- Fork (Linux): <0.5s (workers inherit everything, no imports)
- Spawn (macOS, 2+2 workers): ~1.6s
- Spawn (macOS, 12+12 workers): ~4s locally, much worse on CI

## CI-Specific Issues

### 1. Multiple servers sharing one SQLite DB (task tests)
The task management tests start 3 CodeChecker servers on the same
workspace with the same `config.sqlite`.

**Observed**: Servers waited 165+ seconds then tests failed.
**Verified locally**: Sequential starts work fine. Parallel starts
crash with "table db_version already exists" — a race condition
in Alembic migrations when two processes see "schema is missing"
simultaneously.

**Real CI issue**: Servers likely crash silently during spawn
worker initialization (DupFd, socket passing, or DB migration
issue specific to macOS CI). The `wait_for_server_start` function
then loops for 165 seconds (5-minute timeout) waiting for a
"Server waiting" message that never appears.

**Mitigation**: Tests skipped on macOS. A better fix would be to
check for ERROR in the output file during wait_for_server_start
and fail fast.

**Verified NOT the cause**: SQLite lock contention on sequential
starts — works perfectly when servers start one at a time.

### 2. OAuth mock server startup race
The OAuth mock server needs longer to start on slow CI runners.
A fixed `sleep(5)` is insufficient; increased to 10s on macOS.

### 3. Task timing assumptions
Tests like `createDummyTask(2); sleep(1); assert RUNNING` assume
the task worker picks up and starts the task within 1 second.
With spawn, worker startup adds latency. These tests are
incompatible with spawn without redesign.

## Key Bottleneck: NOT spawn itself

The actual spawn overhead is small (~0.5s per worker). The real
bottlenecks on CI are:

1. **Server crash without fast-fail** - if the server dies during
   startup, `wait_for_server_start` silently waits up to 5 minutes.
   This makes failures appear as "slowness" when it's really a
   crash. All 165-second waits on CI are likely server crashes.
2. **SQLite race on parallel starts** - if two servers start
   concurrently on the same workspace, the second sees "schema
   missing" and tries CREATE TABLE simultaneously with the first.
   This causes a crash, not a hang.
3. **Cold import caches** - CI runners have cold filesystem caches,
   making Python module imports slower (~2-3x vs local)
4. **Core contention** - spawning N workers on a 3-core runner
   serializes heavily during the import phase

## Recommendations

### For production use
- Server start with spawn takes ~2-4s. Acceptable for a long-running
  server process.
- Use `--api-handler-processes` and `--task-worker-processes` to
  control worker count explicitly on constrained hardware.

### For CI
- Set `CC_TEST_API_WORKERS=2` and `CC_TEST_TASK_WORKERS=2` via env
  to reduce spawn overhead in test servers.
- Tests that start multiple servers on the same SQLite DB are
  inherently problematic with spawn. Consider PostgreSQL or
  separate workspaces.
- Timing-sensitive tests (task state checks with sleep) should use
  polling loops instead of fixed sleeps.

### For future optimization
- Consider lazy worker spawning (start workers on first request)
- Consider a worker pool warmup approach (pre-import modules in
  a forkserver-like pattern)
- The `SyncManager` in `log_parser.py` is never shut down - each
  `CodeChecker analyze` invocation leaks a manager process
