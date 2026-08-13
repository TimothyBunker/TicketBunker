# Ticket viewer design QA

## Evidence

- Source visual truth: `/home/tim/.codex/generated_images/019ff8cd-d2ab-7fb2-b018-35f42cccd4c5/exec-147d95fd-83a3-402f-9f30-026adbfde514.png`
- Rendered implementation: `/tmp/ticketbunker-option2-desktop-final2.png`
- Full-view comparison: `/tmp/ticketbunker-option2-comparison-final2.png`
- Focused ticket-core comparison: `/tmp/ticketbunker-option2-focus-comparison.png`
- Responsive implementation: `/tmp/ticketbunker-option2-mobile-final2.png`
- Browser: Google Chrome 144.0.7559.59, light theme.
- State: the selected mock's single in-progress ticket and the real TB-1
  in-progress dogfood ticket, with child reports collapsed. The data is not
  identical: the mock deliberately shows acceptance criteria, two branches,
  and verified reports; current TB-1 has no acceptance criteria, one open
  branch, and an older unverified report. QA compares the page structure and
  behavior without treating those honest data differences as visual drift.

## Normalization

- Source pixels: 1487 x 1058.
- Desktop implementation pixels: 1487 x 1058.
- Desktop CSS viewport: 1487 x 1058 at device scale factor 1.
- Mobile implementation pixels: 390 x 844.
- Mobile CSS viewport: 390 x 844 at device scale factor 1, applied through
  Chrome device metrics. Measured `innerWidth`, document width, and body width
  are all exactly 390px.
- No density resampling was needed for the full-view comparison. The focused
  comparison crops the source's ticket core and the implementation's ticket
  core; the latter is scaled to the same 1050px comparison width solely to
  make typography and alignment readable.

## Findings

There are no remaining actionable P0, P1, or P2 findings.

- Fonts and typography: both source and implementation use a restrained
  system sans-serif hierarchy. The implementation preserves the same compact
  heading, label, body, and monospace-code roles, and long dynamic strings wrap
  without truncation. Optical weight remains legible at desktop and mobile.
- Spacing and layout: the desktop keeps the source's main ticket column,
  vertical divider, and narrow lifecycle/branch/details rail. The responsive
  layout stacks the rail after the ticket body with no horizontal overflow.
  Surfaces stay flat: dividers and one restrained border around stateful report
  content, no decorative card grid, shadow, or dashboard chrome.
- Colors and tokens: white ground, dark navy text, muted metadata, familiar
  blue links/selection, and explicit green/yellow/red state labels match the
  selected direction. Every colored state also carries visible text.
- Image quality and assets: the source contains no product imagery or logo.
  No raster, SVG, CSS-art, emoji, or placeholder asset is introduced. The
  source's small avatar and branch glyph embellishments are intentionally
  omitted (P3) because the repository has no icon/avatar system and they are
  unnecessary to identify assignee, owner, or branch.
- Copy and content: labels are direct and standalone: Description, Acceptance
  criteria, Current state, Child reports, Lifecycle, Branches, and Details.
  Empty-state copy is brief. No agent prompt, transcript, or helper prose leaks
  into the default ticket page.
- States and interactions: Current report and branch links open the matching
  collapsed child report; reports and Details use keyboard-native disclosure;
  lifecycle exposes `aria-current="step"`; focus has a visible 3px outline.
  A clean Chrome target reported no runtime or console errors.
- Accessibility: semantic headings, lists, definition lists, native details,
  visible focus, reduced-motion handling, color-plus-text states, text wrapping,
  and exact-width mobile verification passed. HTML/report content is escaped
  before the constrained trust-tag markup is reintroduced.

## Open questions

- None blocking. The selected mock contains a `Next` row, while the current
  report schema exposes `State` rather than a mechanically trustworthy next
  action. The implementation correctly shows State; adding Next should wait
  for a typed report field instead of deriving or inventing one in the viewer.

## Comparison history

1. Initial comparison: the implementation used a bordered 2 x 2 Current state
   grid, which was a P2 hierarchy and density mismatch from the selected mock's
   simple keyed lines. The header also separated assignee awkwardly and Details
   exposed raw ISO timestamps.
2. Fixes: combined status, priority, and assignee into one fact row; formatted
   timestamps for people; changed Current state to one concise, report-derived
   What/Where/Why/State claim per line; added one Open current report link.
3. Post-fix evidence: the final full-view and focused comparisons above show the
   intended two-column composition and linear current-state hierarchy. Exact
   390px capture shows natural wrapping and no page overflow. The report-link
   interaction expands the correct child, keyboard focus is visible, and a
   fresh console inspection is clean.

## Implementation checklist

- [x] Match the selected single-ticket information hierarchy.
- [x] Keep tracker lifecycle distinct from observed branch state.
- [x] Link branches and current state directly to child reports.
- [x] Preserve verification, freshness, and provenance labels.
- [x] Render optional acceptance criteria and extensible metadata.
- [x] Verify desktop, mobile, keyboard, interaction, console, escaping, and
  long dynamic content.

## Follow-up polish

- P3: if TicketBunker later adopts a real icon/avatar system, the branch rows
  could gain a small consistent branch glyph and report owners could gain real
  identities. Do not add isolated approximations just for this page.

final result: passed
