# Lantern role-play evidence

[Open the offline visual report](TicketBunker-audit.html) · [PDF version](TicketBunker-audit.pdf)

[Full findings and reproduction notes](../../roleplay-audit-2026-09-06.md).

Captured September 6, 2026 local time through the actual source-build interface. Dummy data only. Native picker result and report generator are explicit test substitutions; other application commands, databases, Git operations and sharing are real.

## 1. alice onboarding

First-run identity form; both users continued without a live model.

![First-run identity form; both users continued without a live model.](01-alice-onboarding.png)

## 2. active ticket board

Active board: 24 tickets, completed work excluded.

![Active board: 24 tickets, completed work excluded.](02-active-ticket-board.png)

## 3. connect no browser picker

Browser Connect dead end: no native picker, no user-facing explanation.

![Browser Connect dead end: no native picker, no user-facing explanation.](03-connect-no-browser-picker.png)

## 4. repository connected

Connection succeeds after explicitly substituting the native picker's returned path.

![Connection succeeds after explicitly substituting the native picker's returned path.](04-repository-connected.png)

## 5. check validation

Invalid check timeout produces actionable validation.

![Invalid check timeout produces actionable validation.](05-check-validation.png)

## 6. team joined

Alice Owner and Bob Member after UI invitation and joining.

![Alice Owner and Bob Member after UI invitation and joining.](06-team-joined.png)

## 7. member permissions

Bob's shared repository configuration is read-only; his local attachment is editable.

![Bob's shared repository configuration is read-only; his local attachment is editable.](07-member-permissions.png)

## 8. empty filter

Conflicting filters produce an understandable empty state.

![Conflicting filters produce an understandable empty state.](08-empty-filter.png)

## 9. page two

Page two of all tickets contains the remaining five rows.

![Page two of all tickets contains the remaining five rows.](09-page-two.png)

## 10. synthesize without branch

Before synthesis: no false failed-report state; nested Markdown and source comments render.

![Before synthesis: no false failed-report state; nested Markdown and source comments render.](10-synthesize-without-branch.png)

## 11. alice created branch

Alice's confirmed branch creation updates the branch list.

![Alice's confirmed branch creation updates the branch list.](11-alice-created-branch.png)

## 12. bob branch result

Bob's failed creation leaves the confirmation and an empty branch list despite a real Git branch.

![Bob's failed creation leaves the confirmation and an empty branch list despite a real Git branch.](12-bob-branch-result.png)

## 13. first synthesis

First stub synthesis publishes; independent real test failed. This is not an assessment of model quality.

![First stub synthesis publishes; independent real test failed. This is not an assessment of model quality.](13-first-synthesis.png)

## 14. branch retry error

Retry tells Bob to choose an existing branch from the still-empty list.

![Retry tells Bob to choose an existing branch from the still-empty list.](14-branch-retry-error.png)

## 15. editor chat context

Source reference and draft chat remain alongside the editor.

![Source reference and draft chat remain alongside the editor.](15-editor-chat-context.png)

## 16. unsaved edit

Unsaved change replaces subtraction with addition.

![Unsaved change replaces subtraction with addition.](16-unsaved-edit.png)

## 17. unsaved edit lost

Returning to the ticket and reopening silently loses that unsaved edit.

![Returning to the ticket and reopening silently loses that unsaved edit.](17-unsaved-edit-lost.png)

## 18. file saved

Explicit Save writes the fix and confirms its filename.

![Explicit Save writes the fix and confirms its filename.](18-file-saved.png)

## 19. dirty worktree guard

Dirty-worktree guard prevents checkout, but truncates the first filename.

![Dirty-worktree guard prevents checkout, but truncates the first filename.](19-dirty-worktree-guard.png)

## 20. commit staleness

After external Git commit/push and reopening, report correctly says one newer commit.

![After external Git commit/push and reopening, report correctly says one newer commit.](20-commit-staleness.png)

## 21. bob shared report

Bob reads Alice's shared report and discovers his local branch after reopening.

![Bob reads Alice's shared report and discovers his local branch after reopening.](21-bob-shared-report.png)

## 22. resynthesized

Updated report uses the new commit; independent real test now passes.

![Updated report uses the new commit; independent real test now passes.](22-resynthesized.png)

## 23. service offline

Service outage: actionable toast, but green shared indicator remains.

![Service outage: actionable toast, but green shared indicator remains.](23-service-offline.png)

## 24. narrow long description

640px window: long description wraps without horizontal overflow.

![640px window: long description wraps without horizontal overflow.](24-narrow-long-description.png)

## 25. default type failure

Default ticket type causes an internal error after creating/checking out the branch.

![Default ticket type causes an internal error after creating/checking out the branch.](25-default-type-failure.png)

## 26. service recovered

Service restored: successful synchronization reports 30 tickets and 2 reports.

![Service restored: successful synchronization reports 30 tickets and 2 reports.](26-service-recovered.png)

