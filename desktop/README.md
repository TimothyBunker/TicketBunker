# TicketBunker desktop application

`desktop/` is the primary TicketBunker interface: React and TypeScript compiled
by Vite, hosted as a native Tauri 2 application. The same compiled files are
packaged in the Python core and served by `tb view --browser`, so native and
browser use cannot become separate products.

The native application never starts a localhost ticket server. It owns one
frozen `tb _app-worker` child and exchanges bounded, request-ID-correlated JSONL
over private pipes. The worker accepts an explicit action allowlist and routes
every request through the shared Python command registry. Broken responses are
not replayed because a mutation may already have completed. Closing the app
stops the worker.

The Tauri host owns only lifecycle and narrow machine services: its window, the
directory picker, clipboard, guarded HTTPS login links, and the provider-login
pipe. It does not read source, run Git, query SQLite, or publish reports. One
bundled window renders Bunkers, recursive Projects, ticket search/detail,
Ticket/Agent views, lifecycle and branch controls, WHO/WHAT/WHERE/WHY reports,
the ticket coworker, configuration matrices, and the included source editor.
Remote HTML is never loaded into this privileged context.

Linux and Windows are the first desktop targets. Both use the same UI, Python
handlers, and `platform_services.py` boundary for paths, executable names,
command parsing, process flags, and host diagnostics. Linux is built and
exercised locally; Windows is continuously built and tested in CI. WSL is an
ordinary Linux runtime location. If a Linux desktop advertises a stale D-Bus
socket, the host selects GTK's in-memory settings backend before opening its
directory picker, avoiding unrelated `dconf` warning floods. macOS remains in
the release matrix but needs hardware verification.

Opening the app without `--repo` lands on Bunkers. Create makes a local SQLite
Bunker and first Project without touching Git. Join validates an invitation and
requires authenticated service enrollment before installing a replica. The
selected Project's Configure view owns an indexed allowed-repository table;
connecting a local checkout validates and fetches its `origin` without writing
discovery or authority state into it. Legacy companion data appears only as a
read-only import.

Installed users launch **TicketBunker** from the Windows Start menu or Linux
application launcher. The installer supplies that launcher; `Terminal=false`
on Linux and NSIS uses a current-user Start-menu entry on Windows. No project
path, environment variable, shell, Python installation, or Pi installation is
required. Command-line launch flags below are development overrides only.

Configure edits Bunker defaults or a selected Project's pinned snapshot. Fields
and integration matrices are complete revision-checked documents; GitHub,
GitLab, and Jira are prefilled alongside custom integrations. Local Bunkers can
publish locally. Remote replicas remain read-only until their authenticated
service publishes configuration, even if a cached snapshot names a write
capability.

A Bunker page lists its Projects with repository and ticket counts; the ticket
catalog lives inside a selected Project, after that Project's subprojects. Only
the Bunker header exposes remote-service connectivity. Projects never show a
database or remote-service connection action. Their Repositories tab separates
shared canonical remotes from the current computer's local checkout mappings.

Agent setup starts the verified Pi runtime in constrained RPC mode with only
`tb_auth.ts`. The app renders provider login and model selection itself; secret
answers travel from Rust directly to Pi, and Pi alone writes provider
credentials. The Python command dispatcher receives only the validated model
selection. On Windows, the production app, private core, and Pi children all
run without console windows. Choosing no model is an explicit machine-local mode.

Browser fallback is implemented by `ticketbunker/app_server.py`. It binds to
loopback, serves the same Vite output, injects one launch CSRF token, exposes the
same action allowlist, and exits after the last tab closes. It contains no
domain behavior. Native provider login and directory picking are clearly marked
unavailable there; typed paths and non-agent work remain usable.

Settings → System reads cached release state immediately, checks no more than
once per day in native use, and supports an explicit refresh. The core accepts
only a bounded GitHub release document and exact official release URLs. The
selected installer opens in the system browser and is never loaded or executed
inside the privileged application. Tagged Windows releases require repository
code-signing secrets and verify both the frozen core and NSIS installer before
publication; see `docs/release-signing.md`.

## Development

Install UI dependencies and run the native application against a source-tree or
installed core:

```sh
npm install
TB_DESKTOP_CORE="$(command -v tb)" npm run tauri -- dev -- --repo /path/to/project
```

Run the shared UI in a normal browser through the real Python transport:

```sh
npm run build
PYTHONPATH=.. python -m ticketbunker.cli _app-browser-worker \
  --repo /path/to/project --port 8377 --launch=development
```

The normal checks are:

```sh
npm run build
npm test
cargo test --manifest-path src-tauri/Cargo.toml
```

The production bundle embeds frozen `tb` as a Tauri sidecar and the verified Pi
distribution as private resources. `--core` and `TB_DESKTOP_CORE` are explicit
development or managed-install overrides.

After freezing `dist/tb`, build native artifacts with:

```sh
python tools/build_desktop.py --tb dist/tb
```

Tauri runs `npm run build` before packaging. The staging step verifies Pi before
Tauri sees it, and successful installers plus SHA-256 files are copied to
`dist/releases/`. Use `--stage-only` to inspect the ignored sidecar/resource
inputs. The authoritative boundary is `DESIGN.md` section 3.19.
