# TicketBunker desktop shell

This Tauri shell gives the existing TicketBunker interface its own application
window. It starts the frozen `tb` core as a child, waits for the loopback viewer,
loads only that exact origin, and stops the child when the window is destroyed.
The webview receives no Tauri operating-system capabilities.

`tb view` prefers an installed `ticketbunker-desktop` executable and falls back
to the browser when it is absent. `tb view --browser` always uses the fallback.
`tb open` passes a guarded same-origin `/source` route so the included editor
opens directly in the app.

During development, point the shell at the installed or source-tree `tb`:

```sh
npm install
TB_DESKTOP_CORE="$(command -v tb)" npm run tauri -- dev -- --repo /path/to/project
```

The production bundle will place `tb` beside the desktop executable. `--core`
and `TB_DESKTOP_CORE` are explicit development and managed-install overrides.

Linux builds require Tauri's WebKitGTK prerequisites. The release workflow will
build each desktop target on its native runner rather than cross-compiling it.

The desktop boundary and first-run target are authoritative in `DESIGN.md`
section 3.19. In particular, any future native project picker must remain in a
separate local webview and must never navigate that privileged window to ticket
HTML.
