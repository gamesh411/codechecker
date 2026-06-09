# Unify multiprocessing via multiprocess package

All multiprocessing in CodeChecker uses the `multiprocess` package (dill-based fork of stdlib). This replaces the platform-conditional compatibility module with a single import path.

## Considered Options

| Option | Verdict |
|--------|---------|
| stdlib `multiprocessing` with platform defaults | macOS defaults to spawn since Python 3.8; stdlib pickle cannot serialize SQLAlchemy engines, bound methods, closures passed to Pool workers |
| `multiprocess` with platform defaults | **Chosen.** dill handles complex objects in Pool workers; defaults to fork on all POSIX platforms (including macOS), matching the server's pre-fork worker model |
| `multiprocess` + explicit spawn everywhere | Breaks server -- SQLAlchemy engines contain weakrefs (`KeyedRef`) that even dill cannot pickle |

## Key Insight

The `multiprocess` package defaults to **fork** on all POSIX platforms (Linux and macOS), unlike stdlib which changed macOS default to spawn in Python 3.8. This fork default is intentional and matches CodeChecker's server architecture (pre-fork workers sharing bound sockets and SQLAlchemy engines).

Dill's value is for `Pool` workers in the analyzer, which pass closures and initializer functions that stdlib pickle rejects.

## Consequences

- No `set_start_method` call needed -- multiprocess fork default works on all POSIX platforms.
- Server pre-fork worker model unchanged -- fork avoids serializing unpicklable weakref state.
- Analyzer `Pool` workers benefit from dill serialization for closures and bound methods.
- `codechecker_common/compatibility/multiprocessing.py` removed -- one import path everywhere.
- Test helper closures (e.g. `start_server_proc`) work without refactoring since dill pickles them.
- One extra dependency (`multiprocess~=0.70`, pulls in `dill`) but it was already present.
- Windows support would require restructuring the server to not rely on fork (separate effort).
