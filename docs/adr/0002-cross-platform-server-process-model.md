# ADR-0002: Cross-Platform Server Process Model

## Status

Proposed

## Context

CodeChecker's server uses a pre-fork worker model: the main process creates
an `HTTPServer` (binds socket, creates SQLAlchemy engine), then forks N worker
processes that each call `serve_forever()` on the inherited server object.

This works on Linux but has conflicts with macOS and Windows.

### Problem 1: macOS Obj-C fork safety

Forked workers crash when calling macOS system frameworks (Security.framework
via urllib3 for HTTPS). Affects OAuth token exchange, any outbound HTTPS.

    +[NSCharacterSet initialize] may have been in progress in another thread
    when fork() was called.

**Current fix:** `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` (widely used by
Celery, gunicorn, Django). Effective but masks real fork-safety bugs.

### Problem 2: Windows has no fork()

Windows requires `spawn` start method. All state passed to workers must be
picklable. The HTTPServer object contains a socket (unpicklable OS handle) and
multiprocess.Queue/Value objects (fork-inheritance only).

### Key finding: SQLAlchemy is NOT the blocker

Original assumption was that SQLAlchemy engines can't be pickled. **This is
wrong.** Testing confirms:
- `dill.dumps(engine)` with NullPool + event listeners: **works**
- `dill.dumps(sessionmaker(bind=engine))`: **works**
- Round-trip (dumps + loads + use): **works**

The ACTUAL unpicklable objects are:
1. **socket** — OS file descriptor handle
2. **multiprocess.Queue** — "should only be shared through inheritance"
3. **multiprocess.Value** — same

All three have trivial spawn-compatible alternatives.

### ORM analysis

547 ORM query/session usage lines in the server. Deeply integrated. Replacing
with raw SQL would be a massive effort for zero architectural benefit (since
dill handles ORM objects fine). **The ORM is not a constraint.**

## Component Analysis

### Analyzer (already cross-platform)

- `multiprocess.Pool` for running clang/cppcheck — **no SQLAlchemy at all**
- `SyncManager.DictProxy` for config — **already picklable**
- Only platform blocker: build logging (ld-logger Linux-only, bear for macOS)
- Windows: would need build logging solution (bear, or cmake compile_commands)

### Store Client (already cross-platform)

- `ProcessPoolExecutor` for parsing report files — **pure file I/O**
- No SQLAlchemy, no sockets, no shared state

### Server API Workers (the ONLY blocker)

Workers inherit the full HTTPServer object via fork. They need:
- Listening socket → **pass fd integer, child does socket.fromfd()**
- DB access → **pass SQLServer factory (connection string), create engine in
  worker** (background task workers ALREADY do this correctly)
- Session manager → **serializable config + shared session store (DB-backed)**
- Product registry → **reload from config DB in each worker**

### Background Task Workers (already correct)

These receive `config_db_sql_server` (a factory) and call
`create_engine()` in the child. This is the **correct pattern** that API
workers should follow.

## Proposed Architecture: Spawn-Compatible Server

```
Main Process
├── Bind socket, get fd
├── Create SyncManager (Queue, Value, dict proxies)
├── Start N API workers (spawn):
│   Worker receives: (socket_fd, db_url, config_dir, manager_proxies)
│   Worker creates: own socket.fromfd(), own engine, own HTTPServer
│   Worker runs: serve_forever()
├── Start M Background workers (spawn):
│   (already works this way)
└── Signal handling, child monitoring
```

### Migration Steps (bounded refactor)

1. **Replace `Queue()` and `Value()` with `SyncManager.Queue()` and
   `SyncManager.Value()`** — these are already picklable proxies.
   The SyncManager is already used for `task_pipes`. Minimal code change.

2. **Extract worker entry point function** that takes serializable args:
   ```python
   def api_worker_main(socket_fd, db_connection_string, config_dir,
                       task_queue, task_pipes, shutdown_flag, machine_id):
       sock = socket.fromfd(socket_fd, socket.AF_INET, socket.SOCK_STREAM)
       engine = create_engine(db_connection_string, poolclass=NullPool)
       session_factory = sessionmaker(bind=engine)
       # Create HTTPServer using the shared socket
       server = CCSimpleHttpServer.from_socket(sock, session_factory, ...)
       server.serve_forever()
   ```

3. **Add `CCSimpleHttpServer.from_socket()` class method** that constructs
   the server from a pre-bound socket + config, instead of binding itself.

4. **Move session store from in-memory dict to DB-backed** — the
   `SessionManager` currently stores active sessions in a Python dict shared
   via fork. For spawn, store in the config SQLite DB (or Redis for
   production PostgreSQL deployments). This also fixes session loss on worker
   crash (current bug).

### What stays the same

- All ORM models, queries, migrations
- All Thrift API handlers
- All analyzer multiprocessing
- Store client multiprocessing
- Background task workers

### Effort estimate

- Step 1 (Queue/Value → Manager proxies): ~20 lines changed
- Step 2 (worker entry point): ~80 lines, new function
- Step 3 (from_socket): ~30 lines, new classmethod
- Step 4 (session store to DB): ~100 lines, most complex

Total: ~230 lines of focused refactoring. No full rewrite needed.

## Decision

**Short term (this PR):** `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES` in
`start_server()`. Proven workaround, used by major Python server projects.

**Medium term:** Refactor API workers to spawn model (Steps 1-4 above).
This enables:
- Windows server support
- Eliminates macOS fork-safety workaround
- Fixes session-loss-on-worker-crash bug
- Aligns API workers with background workers (consistent architecture)

**Not needed:**
- ORM replacement (not the blocker)
- Async rewrite (too invasive, Thrift has no async support)
- Thread-based workers (loses process isolation for crash resilience)

## Platform Support After Full Migration

| Component       | Linux | macOS | Windows |
|-----------------|-------|-------|---------|
| Analyzer        | yes   | yes   | yes*    |
| Store client    | yes   | yes   | yes     |
| Server          | yes   | yes   | yes     |
| OAuth           | yes   | yes   | yes     |
| Build logging   | ld-logger | bear | cmake** |

*Windows analyzer needs build logging solution
**cmake --export-compile-commands or bear-for-windows

## Key Finding: Sessions Are Already DB-Backed

Investigation revealed that `SessionManager.__sessions` is only a per-process
cache. Sessions are already persisted to the config DB (`SessionRecord` table):
- `create_session()` writes to DB immediately
- `get_session()` falls back to DB lookup on cache miss
- `invalidate()` removes from both cache and DB

This means spawn workers can share sessions without any schema migration.
Each worker has its own empty cache on start, and discovers sessions via the
shared DB. **Step 4 (session store migration) is not needed.**

## Current Status (spawn-migration-1, spawn-migration-2)

Completed groundwork:
1. Queue/Value → SyncManager proxies (picklable for spawn)
2. Module-level `_api_worker_main` entry point (serializable target)

Remaining for Windows spawn support:
3. Pass connection string + config paths to worker instead of server object
4. Worker reconstructs: engine, SessionManager, HTTPServer from socket fd
5. Use `socket.share()`/`socket.fromshare()` for Windows socket passing

These steps require Windows CI for testing and are not part of this PR.
