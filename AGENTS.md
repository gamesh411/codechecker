# AGENTS.md

## Cursor Cloud specific instructions

CodeChecker is a static-analysis infrastructure (Python CLI/analyzer + Thrift web
server + Vue 3 web UI), orchestrated by GNU Make. General build/usage docs live in
`docs/README.md` and `CONTRIBUTING.md`; the notes below only capture non-obvious,
durable gotchas for this environment.

### Environment / how things are wired
- A single root dev virtualenv at `venv_dev` is the source of truth. Always work with
  it: `source venv_dev/bin/activate`. The startup update script creates it
  (`make venv_dev`) and adds the auth deps (`python-ldap`, needed by the web server
  unit tests) into it.
- The CLI is run from a built package, not the source tree. After building, put it on
  PATH: `export PATH="$PWD/build/CodeChecker/bin:$PATH"`. Then `CodeChecker ...` works.

### Building
- Build with `BUILD_LOGGER_64_BIT_ONLY=YES make package`. Plain `make package` also
  builds a 32-bit `ldlogger` and fails here because 32-bit libs
  (`gcc-multilib`/`libc6-dev-i386`) are intentionally not installed. The build also runs
  `npm install` + webpack build for the Vue UI (slow, a few minutes).
- `make package` copies Python source into `build/`, so later edits to source files are
  NOT picked up until you rebuild. For live-editing of Python source use
  `BUILD_LOGGER_64_BIT_ONLY=YES make dev_package`, which symlinks the source packages
  into the build dir instead of copying.

### Testing (important: do NOT use the `*_in_env` targets)
- Run tests with the venv activated and the NON-`_in_env` targets, matching CI:
  `source venv_dev/bin/activate && export PATH="$PWD/build/CodeChecker/bin:$PATH"`,
  then e.g. `make test_unit`.
- The `*_in_env` targets (e.g. `make test_unit_in_env`) create isolated per-component
  venvs (`analyzer/venv_dev`, `web/venv_dev`) that do NOT install the auth deps, so the
  server LDAP unit test (`server/tests/unit/test_ccldap.py`) fails there with
  `ModuleNotFoundError: No module named 'ldap'`. Use the plain targets with the root
  `venv_dev` activated instead.
- Lint: `make pycodestyle` and `make pylint` (Python); UI lint:
  `cd web/server/vue-cli && npm run test:lint`. UI unit tests: `npm run test:unit`.
  UI e2e (`npm run test:e2e`) needs Selenium + a browser and is optional.

### Running the product
- Web server: `CodeChecker server` (SQLite by default, listens on `localhost:8001`;
  health endpoints `/live` and `/ready`). Use `-w <dir>` to set the workspace.
- Typical end-to-end flow: `CodeChecker check -b "<build cmd>" -o ./results` (or
  `log` + `analyze`), then `CodeChecker store ./results -n <run> --url
  http://localhost:8001/Default`, then browse the run in the web UI.
- Available analyzers here: clangsa, clang-tidy, cppcheck, gcc. `infer` is not installed
  (optional). PostgreSQL is optional; SQLite is the default and needs no service.
- Vue UI dev server (hot reload, separate from the packaged static UI):
  `cd web/server/vue-cli && npm run server`.
