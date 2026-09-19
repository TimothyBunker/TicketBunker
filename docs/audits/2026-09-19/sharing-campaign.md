# Two-participant browser campaign

Run after `npm ci --prefix desktop` and `npm run build --prefix desktop`:

```sh
npm run test:sharing --prefix desktop
```

Requires Playwright Chromium. `TB_SIMULATION_OUTPUT` chooses the screenshot and
result directory; otherwise it is `ticketbunker-sharing-campaign` under the OS
temporary directory. The disposable lab retains its isolated working directories
for investigation. Its private `lab.json` contains test credentials: do not publish
that file. CI uploads only screenshots and the result/failure log directory.

## Scenarios

- [x] Owner creates a Bunker, configures repository availability and shares it.
- [x] Member joins by invitation and maps an independent local checkout.
- [x] Shared checks arrive; member cannot edit owner configuration.
- [x] Service commits a save but loses the acknowledgement. Explicit Sync
  reconciles the original request without another server change.
- [x] Service is offline during save. Closing the form does not lose the durable
  intent; reconnect/Sync delivers it.
- [x] Two owner windows race an edit. Local stale revision is rejected before
  transmission and the winner remains intact.
- [x] Independent replicas race branch links. Conflict remains after pull;
  reviewed retry preserves both branch links and clears the unfinished operation.
- [x] Synthesize uses normal report gates; another participant reads the report.
- [x] Persisted private coworker conversation, unsent message, unsaved source
  edit and absolute checkout paths do not appear in the shared snapshot.
- [x] No uncaught browser exception; header title remains in its content column.

The real UI, app workers, service authorization, SQLite, Git and report gates
are exercised. The model is deterministic test output, not live Pi. Service
interruption and a deliberately dropped response are fixture-only injections.
This does not substitute for native Windows installation or real-provider tests.

## Fix found

The transfer indicator had become a third child in a two-column ticket header,
placing the title under the back button and overlapping the chapter navigation.
Back and transfer controls now share one column; a browser geometry assertion
protects the title's width and containment. Earlier harness failures involved
accessible-name matching and async onboarding, not additional product defects.
