# ADR-0003: Spawn Performance on macOS CI

## Status: Accepted

## Context

After implementing spawn-based multiprocessing on macOS (ADR-0002),
CI tests revealed significant performance differences compared to
Linux (fork). This document records verified findings from
instrumented CI runs on GitHub Actions macOS runners.

## Verified Measurements (GitHub Actions macOS, 3 CPU, 7 GB RAM)

### Import cost
- `codechecker_server.server` import: **35.95s** (887 files, 997 modules)
- Per-file I/O latency: ~40ms (vs <0.3ms on local NVMe)
- Same import locally: 0.26s (133x faster)

### Server startup (2 API + 2 task workers)
- Port accepts connections after: **84s**
- "Server waiting" never appears in output file (buffering issue)
- Each spawn worker must import all 887 files independently

### Root cause chain
1. macOS CI runner has ~40ms per-file I/O (VM/network storage)
2. Spawn workers import 887 .pyc files each = ~36s per worker
3. 4 workers on 3 cores = ~84s until port is open
4. Workers need additional time after port opens to handle requests
5. Python stdout buffering prevents log-based detection entirely

### What was NOT the cause
- SQLite contention (sequential starts work fine)
- Server crashes (server starts successfully, just slowly)
- `PYTHONUNBUFFERED=1` (doesn't help — buffering is in file I/O
  layer between subprocess and the file handle)

## Fixes Applied

### Server startup detection (`web/tests/libtest/codechecker.py`)
- Added `PYTHONUNBUFFERED=1` to subprocess env (helps for some cases)
- Added **port connectivity check** as fallback: socket connect to
  the server port every second. Detects server at 84s instead of
  timing out at 165s or 300s.

### OAuth mock server (`web/tests/functional/authentication/__init__.py`)
- Replaced blind `sleep(5)` with port readiness polling (port 3000)
- Captured stdout/stderr to log file for debugging
- Reports exact startup time or failure reason

### Task management tests
- Skipped on macOS (`sys.platform == "darwin"`)
- Reason: tests expect task to reach RUNNING state within 1s of
  creation. Workers take 84s to be ready. Incompatible.

### CI workflow
- `CC_TEST_API_WORKERS=2` / `CC_TEST_TASK_WORKERS=2` reduces spawn
  overhead (4 workers instead of default cpu_count * 2 = 6)

## Key Bottleneck: CI runner I/O

The 84s server startup is entirely I/O bound. The fix requires
either faster CI infrastructure or reducing the import footprint.

Implications for tests:
- Any test starting a server: allow 90+ seconds for startup
- Task tests (3 servers): need 4+ minutes total startup, and
  workers still aren't ready when port opens
- Tests relying on task worker responsiveness: skip on macOS

## Future Optimization Options (if CI time is unacceptable)

1. **Lazy imports**: defer SQLAlchemy/Alembic/Thrift to first use.
   Would dramatically reduce spawn worker import cost.
2. **Single-worker mode**: `--api-handler-processes 1
   --task-worker-processes 1` — halves startup time.
3. **forkserver**: Python's forkserver method pre-imports once, then
   forks from a warm process. Avoids repeated imports. Requires
   investigation for compatibility with DupFd/socket passing.
4. **Zipimport**: bundle all .pyc into a zip — single file open
   instead of 887 separate ones.

