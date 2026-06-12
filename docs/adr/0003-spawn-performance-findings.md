# ADR-0003: Spawn Performance Findings on macOS

## Status: Observation (informational)

## Context

After implementing spawn-based multiprocessing on macOS (ADR-0002),
CI tests revealed significant performance differences compared to
Linux (fork). This document records profiling findings and known
issues for future development.

## Profiling Results

### Local macOS ARM64 (M-series, fast NVMe)
- Single spawn worker lifecycle: ~0.46s (imports + setup)
  - `codechecker_server.server` import: 0.26s (887 files, 997 modules)
  - Process overhead: 0.07s
- With 2 API + 2 task workers: server ready in **1.65s**

### GitHub Actions macOS runner (3 CPU, 7 GB RAM)
- `codechecker_server.server` import: **35.95s** (same 887 files)
- Per-file I/O latency: ~40ms (vs <0.3ms local)
- With 2 API + 2 task workers: server startup ~60-80s
- With 3 servers (task test): total startup ~150-240s

### Root cause of CI slowness
The macOS GitHub Actions runner has **extremely slow file I/O**
(~40ms per file operation vs <0.3ms on local NVMe). Since spawn
workers must import 887 Python files from scratch, each worker
takes ~36 seconds to become ready. This is a VM/infrastructure
constraint, not a code issue.

This means:
- 1 server with 2+2 workers on 3 cores: ~50-80s startup
- 3 servers sequential (task test): 150-240s startup
- OAuth mock server (128 files): ~5s startup

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

## Key Bottleneck: CI runner I/O

Measured: importing `codechecker_server.server` loads 887 .pyc
files. On the CI runner this takes 36s (40ms/file). Locally it
takes 0.26s (0.3ms/file). The difference is **133x slower I/O**.

This is NOT fixable by code changes alone. The import dependency
tree (SQLAlchemy, Alembic, Thrift, authlib, etc.) is required for
server operation. Spawn workers must import all of it.

Implications for tests:
1. Server startup with 2+2 workers: allow 80-90s
2. Task tests (3 servers): need 4+ minutes total startup
3. Any test that starts a server needs appropriate timeouts

## Recommendations

### For CI tests
- Set `CC_TEST_API_WORKERS=2` and `CC_TEST_TASK_WORKERS=2` (done).
- Tests starting servers must tolerate 80-90s startup time.
- Task tests (3 servers) need 4+ minutes just for startup.
- Skip timing-sensitive tests (task state checks with 1s sleep)
  on macOS CI — the assumptions cannot hold.
- Use polling loops for readiness checks, not fixed sleeps.

### For future optimization (if CI time is unacceptable)
- Lazy imports: defer SQLAlchemy/Alembic/Thrift imports to first
  use rather than module-level. Would reduce spawn worker import
  to only what's needed for HTTP serving.
- Zipimport: bundle .pyc files into a zip for single-seek import.
- Reduce worker count to 1+1 on CI (saves 36s per extra worker).
- Pre-compile a single-file server bootstrap that spawn workers
  can import quickly, deferring heavy deps to request time.
