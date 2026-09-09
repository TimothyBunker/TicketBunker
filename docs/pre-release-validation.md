# Pre-release reliability validation

This pass changes source and bundled browser assets, not the released installer.
Validation on 2026-09-08: 386 Python tests, 81 UI tests, 9 Rust tests, production
frontend build, and the real-browser hardening flow passed.

## Repeatable checks

From the repository:

```sh
PYTHONPATH=. python -m unittest discover -s tests -q
cd desktop
npm run build
npm test
npm run test:hardening
cargo test --manifest-path src-tauri/Cargo.toml --lib
```

Recovery scenarios in `tests/test_sync_recovery.py` use scratch databases,
fake tracker responses, and a real local sharing service. They exercise failure
after a prior success, reopening through a fresh worker process, process exit
after a SQLite INSERT before commit, and a lost acknowledgement after service
acceptance. Retry must preserve existing tickets, apply updates once, and avoid
duplicate tickets. The shared lost-acknowledgement path deliberately reconciles
the Bunker before another tracker import.

The browser flow tests ticket-sync failure/retry and reloads the page to prove
the success tooltip does not depend on the old in-memory cache. UI tests cover
neutral cached Git refs, loading, explicit failure, and component-version mismatch.

## Native Linux smoke fixture

After installing the source `tb` entry point and building the frontend:

```sh
cargo build --manifest-path desktop/src-tauri/Cargo.toml --features tauri/custom-protocol
PYTHONPATH=.:tests python tests/native_smoke_fixture.py desktop/src-tauri/target/debug/ticketbunker-desktop
```

The fixture prints its scratch paths. It isolates tracker and Pi credentials,
uses a local Git origin, and does not edit the real project. Optional `--editor`
opens the scratch README directly. Type `reopen` after closing the app to launch
against the same data; type `quit` to stop and clean up. No model call or provider
login is required. This is a manual fixture, not an automated native certification.

Observed on Linux in this pass:

- Native app startup and ticket navigation; source opening in the included editor.
- System interface/backend version display.
- Real bundled Pi provider catalog and in-app login-method prompt; cancelled
  before authenticating.
- Native folder picker opens and cancels; invalid selection is rejected without
  replacing the existing repository attachment.
- Native window-manager close request with a dirty file opens the unsaved-changes
  dialog. Save writes before closing; reopening displays the saved draft.

## Outstanding installer checks

The default WebKit renderer showed focus artifacts and severe visual corruption
in this Linux graphical environment. Repeating with
`WEBKIT_DISABLE_DMABUF_RENDERER=1` cleared the observed artifacts, and the native
save/close/reopen check passed with that diagnostic override. The native launcher
now selects this fallback automatically when the kernel release identifies WSL,
unless the user explicitly set that variable already. WebKit checks this setting
before selecting its buffer transport ([upstream source](https://github.com/WebKit/WebKit/blob/main/Source/WebKit/UIProcess/gtk/AcceleratedBackingStore.cpp)).
Ordinary Linux defaults and native Windows are unchanged. This fix belongs to
the app, not the user's WSL browser. Native Linux outside WSL and the actual next
installer still need their own visual smoke checks.

Follow-up validation: 12 Rust tests and the native debug build passed. Launching
the scratch fixture with `WEBKIT_DISABLE_DMABUF_RENDERER` absent from the parent
environment confirmed that the WebKit child received `1` from the app. The
WSL/X11 editor, focused controls, native unsaved-close dialog, Save and reopen
rendered cleanly; the saved text was checked on disk and in the reopened window.
This was a real native-window test, not a browser or mocked transport check.

Still required: successful folder selection through the native picker, completed
provider authentication using a consenting test account, and the fresh Windows
installer path. Recheck these against the actual next installer, not just the
source worker. Matching package versions do not distinguish two source builds
that retained the same version number.
