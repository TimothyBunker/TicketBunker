# Lantern Robotics: two-user interface exercise

Audit date: September 6, 2026 (local). Source build, local Chrome through
Playwright; two browser contexts, two app workers, separate local databases,
separate Git clones, one real local TicketBunker sharing service. Coal and real
credentials were not used. No production fixes are part of this audit.

The fixtures are 30 made-up navigation tickets, Alice (owner) and Bob (member),
and a small Python warehouse-navigation project with a deliberately failing test.
The initial ticket input is seeded through typed backend commands, not the UI.
All subsequent product actions described below use rendered UI controls.
The report generator is `tools/stub_agent.py`: publication gates and checks run,
but this does not grade model reasoning or authenticate a real model.

Reproduction helpers: `tools/roleplay_lab.py` and
`desktop/tests/roleplay-driver.mjs`. Current retained laboratory:
`/tmp/ticketbunker-roleplay-qe_iwdms`. Its private `lab.json` contains a setup
token: do not commit or share it. Screenshots mask invitations and password inputs.

## Journeys and evidence

Screenshots are preserved in [the evidence gallery](audits/lantern-roleplay/README.md),
as well as in `screenshots/` under the laboratory directory. Each numbered image
was opened and inspected before accepting it. The helpers are interactive lab
tools, not a claim that this UI journey is a committed unattended regression test.

| Step | Role-play | Result | Screenshot |
| --- | --- | --- | --- |
| 1 | Alice and Bob set their identities; choose Use without agent | Passed. Browser model login explicitly unavailable; real model onboarding not tested. | 01-alice-onboarding.png |
| 2 | Alice creates Bunker/project; navigate seeded board | Passed: 24 active tickets, completed tickets excluded. | 02-active-ticket-board.png |
| 3 | Configure allowed repository and connect Alice's clone | Browser blocked: Connect silently does nothing without the native folder dialog. Continued with only a native-picker-result shim; attachment, validation and Git fetch remain real. | 03-connect-no-browser-picker.png, 04-repository-connected.png |
| 4 | Publish project check with invalid timeout, then correct it | Passed: clear 30–3600 second validation, then save succeeds. | 05-check-validation.png |
| 5 | Alice shares Bunker and invites Bob; Bob joins | Passed: shared tickets/settings appear, both members listed. | 06-team-joined.png |
| 6 | Bob attempts to configure shared repositories; attaches his own clone | Passed: shared settings read-only, local attachment allowed. No privilege-bypass testing implied. | 07-member-permissions.png |
| 7 | Combine incompatible assignee/label filters | Passed empty state; no one-click reset. | 08-empty-filter.png |
| 8 | Show all statuses, next page, search from page two | Passed: 25+5 rows, search resets to matching NAV-1. Status-group counts describe current page, not total board. | 09-page-two.png |
| 9 | Open ticket before synthesis | Clear Not synthesized state, Markdown nested bullets and source comment render. Synthesize on main publishes nothing; transient feedback was not captured. | 10-synthesize-without-branch.png |
| 10 | Alice creates ticket branch after confirmation | Passed: actual branch checked out, immediately listed, no report yet. | 11-alice-created-branch.png |
| 11 | Bob creates another branch while shared data has changed | Failed recovery: actual Git branch exists, but screen remains confirmation with no branches. Retry says choose the existing branch from an empty list. Initial failure toast was missed; do not infer its precise wording from the retry. | 12-bob-branch-result.png, 14-branch-retry-error.png |
| 12 | Alice presses Synthesize | Gated stub report published; actual `python3 check.py` fails (exit 1 in check observation). UI shows configured command but no deterministic pass/fail result. Stub Standing is not evidence of model quality. | 13-first-synthesis.png |
| 13 | Send source reference to chat, type draft, open editor | Passed: source quote and chat drawer remain alongside editor. Sending to a real model is deliberately not exercised. | 15-editor-chat-context.png |
| 14 | Edit code without saving; return to ticket; reopen file | **Failed: draft silently lost**, no confirmation. Repeated twice: `+` reverts to saved `-`; Save becomes disabled. Chat draft remains. | 16-unsaved-edit.png, 17-unsaved-edit-lost.png |
| 15 | Save the fix, try creating another branch with dirty worktree | Guard passed, code saved on current branch. Error says commit/stash; filename incorrectly rendered `avigation.py` instead of `navigation.py`. | 18-file-saved.png, 19-dirty-worktree-guard.png |
| 16 | Commit/push using normal external Git; reopen ticket | Passed: report says one commit since synthesis; origin branch appears. Git commit/push are explicitly outside the UI, not claimed as app features. | 20-commit-staleness.png |
| 17 | Bob syncs Bunker and opens Alice's report | Passed shared report read; reopening also discovers Bob's formerly invisible local branch. Alice's report appears as report-only until Bob's Git refs are fetched. This is a workaround for visibility, not proof the failed branch-link publication recovered. | 21-bob-shared-report.png |
| 18 | Alice updates synthesis after fix | Passed: report anchored at new commit, zero commits since; real test observation has exit 0 and `passed: true`. Check result still absent as an independent UI indicator. | 22-resynthesized.png |
| 19 | Stop shared service, Bob presses Sync now | Cached Bunker remains readable and concise network error gives address/retry. Last successful sync timestamp stays, but no persistent offline indicator after five-second toast disappears. | 23-service-offline.png |
| 20 | Read cached tickets while offline; narrow long-description ticket to 640px | Passed: ticket opens offline; long text wraps, measured horizontal overflow 0px. Details and controls stack into a very long page; no mobile/native-device test implied. | 24-narrow-long-description.png |
| 21 | Create a branch for NAV-4, which omitted its type at intake | **Failed after Git side effect**: generic diagnostic toast, actual `bob/NAV-4-default-type-test` checked out, list still empty. Diagnostic identifies `IntakeRejected` in assignment intake; runtime ticket defaults to `Task`, incompatible with legacy lowercase contract. | 25-default-type-failure.png |
| 22 | Restart service and Bob retries Sync now | Passed: explicit confirmation of 30 tickets and 2 reports. Both source worktrees clean at end. | 26-service-recovered.png |

## Highest-impact findings

1. Protect unsaved source drafts on navigation. This is lost user work, not just
   confusing copy. Preserve the draft or ask before discarding it.
2. Reconcile default ticket types before creating a Git branch. An omitted type
   must not become a late internal error after the source checkout has changed.
3. Refresh local branch state even when shared publication fails, and expose the
   advertised recovery action. A retry must not tell users to pick an invisible
   branch. Branch creation and shared attachment are separate outcomes.
4. Surface deterministic check results separately from generated narrative. A
   configured test is not proof of a passing test; its exit status is known.
5. Browser repository connection needs a supported picker/path alternative or an
   explicit explanation. A native-only control should not silently do nothing.

Additional recovery polish: persistent failed-sync state (the green shared dot
and last-sync label remain after an unsuccessful sync), non-expiring actionable
errors, correct first filename in the dirty-worktree message, and accessible
names on the editor input. The report column's very large empty spaces are
visible with concise stub text; realistic model prose may change that layout.

## Running another laboratory

From the repository root, with the Python core and desktop dependencies already
available:

```sh
PYTHONPATH=. python tools/roleplay_lab.py
```

It prints a disposable root and two app addresses. In another terminal:

```sh
cd desktop
node tests/roleplay-driver.mjs /tmp/<printed-laboratory-name>/lab.json
```

The browser helper currently expects `/usr/bin/google-chrome`. It accepts JSON
DOM actions on stdin; the laboratory accepts fixture, Git and service controls.
Create the Bunker/project through Alice's UI before submitting `{"op":"seed"}`
to the lab. Submit `{"op":"quit"}` to both helpers when finished. The native
picker shim is explicit (`{"op":"picker","user":"alice"}`), not silently
installed. It only supplies that participant's dummy source directory.

This run stopped both helpers and all three service listeners after capture.
The retained lab includes local databases/source clones for diagnosis; no real
project, external Git remote, or model account was modified. Screenshots and
notes, not databases or credentials, are preserved in the repository.

Supporting verification: `PYTHONPATH=. python -m unittest discover -s tests -p
test_two_participant_loop.py -v` completed: two tests, one passed (17 isolated
participant checks inside it), one explicitly expected failure for the default
type mismatch. This is **not** a green result for that known defect. Browser
helper JavaScript syntax check and `git diff --check` also passed. No full suite
or installed desktop build was run during this UI-only audit.

## Limits

This exercises the actual web interface and workers, not installed Tauri on
Windows/macOS, real provider authentication, a real network SSH server, or Jira.
No claim of accessibility compliance: screenshots and DOM show names/structure,
but screen readers, contrast measurement and full keyboard navigation remain.
Source editor textarea has no visible/accessibility label in its DOM; chat's
placeholder supplies context but is not an explicit label.
