# TicketBunker desktop shell

This Tauri shell gives the existing TicketBunker interface its own application
window. It starts the frozen `tb` core as a child, waits for the loopback viewer,
loads only that exact origin, and stops the child when the window is destroyed.
The webview receives no Tauri operating-system capabilities.

Launching the executable without `--repo` opens the bundled Bunkers window.
Create Bunker makes one local SQLite database and a first Project without
touching Git. Join Bunker inspects a bounded invitation, then requires remote
authentication and enrollment before installing a local replica. Each Bunker
renders its recursive Project tree; only an explicit Attach repository action
opens the native directory picker, validates the checkout through the Python
action registry, and records its device-local path. Existing companion-backed
projects appear only in the read-only Migration section. A stale or missing
Linux session D-Bus socket selects GTK's in-memory settings backend so the
native picker remains quiet and responsive.

Agent setup is also owned by the Bunkers window. It starts the verified Pi
runtime in constrained RPC mode with only `tb_auth.ts`, renders provider login
as native forms, and sends secret responses straight from Rust to Pi. The
Python core never receives provider secrets. Closing the dialog kills that Pi
child; choosing no model records an explicit machine-local opt-out.

`tb view` prefers an installed `ticketbunker-desktop` executable and falls back
to the browser when it is absent. `tb view --browser` always uses the fallback.
`tb open` passes a guarded same-origin `/source` route so the included editor
opens directly in the app.

During development, point the shell at the installed or source-tree `tb`:

```sh
npm install
TB_DESKTOP_CORE="$(command -v tb)" npm run tauri -- dev -- --repo /path/to/project
```

The production bundle embeds `tb` as a Tauri sidecar and the verified Pi
distribution as private application resources. `--core` and `TB_DESKTOP_CORE`
are explicit development and managed-install overrides.

Linux builds require Tauri's WebKitGTK prerequisites. The release workflow will
build each desktop target on its native runner rather than cross-compiling it.

After freezing `dist/tb`, build the platform-native artifacts with:

```sh
python tools/build_desktop.py --tb dist/tb
```

The staging step checks the pinned Pi archive before Tauri sees it. Successful
builds copy the installer and an adjacent SHA-256 file into `dist/releases/`.
Use `--stage-only` to inspect the ignored `src-tauri/binaries/` and
`src-tauri/resources/` inputs without running the native bundler.

The desktop boundary and first-run target are authoritative in `DESIGN.md`
section 3.19. In particular, the native Bunker launcher remains in a separate
local webview and must never navigate that privileged window to ticket HTML.
The same rule confines agent-setup events to that window.
