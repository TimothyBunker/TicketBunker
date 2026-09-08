# TicketBunker Product

## Register

product

## Users

Engineers, technical leads, and project contributors working across one or
more source repositories. Their primary job is to open one ticket, understand its
current truth, find the relevant branch and report, and continue or review the
work without reconstructing history by hand.

## Product Purpose

A Bunker is the product's configuration, membership, and synchronization
boundary. It always has one device-local database and may connect through a
TicketBunker service API to team-owned remote storage. Projects form a hierarchy
inside the Bunker; Git repositories are attached resources rather than the
database or authentication system. TicketBunker combines the familiar
information anatomy of an agile ticket with Git lifecycle state, branches,
verified reports, observations, and extensible metadata. Success means a
contributor can understand and act on a ticket without needing the originating
agent session or knowing which provider, Git executable, or database implements
the boundary.

## Brand Personality

Direct, calm, trustworthy.

The interface should feel familiar to Jira users while making repository proof
and report provenance easier to reach. It should disappear into the task.

## Anti-references

- Generic analytics dashboards with metrics that do not help complete a ticket.
- Card grids, nested panels, decorative status treatments, and excessive chrome.
- Helper text that compensates for unclear labels or hierarchy.
- Agent-first interfaces that expose prompts, transcripts, or raw reasoning by default.
- Flashy motion, gradients, glass effects, and ornamental visuals.

## Design Principles

1. The Bunker is the authority. A local database always exists; an authenticated remote service becomes team authority only when configured. Git and trackers are attached sources, never membership proof.
2. Design one ticket first. Global search and portfolio views come after the singular ticket works.
3. Make the next useful object obvious. Branches and child reports stay visible and directly accessible.
4. Put proof beside claims. Verification, freshness, and provenance are visible without dominating the page.
5. Reveal depth progressively. Metadata, activity, and full reports remain available without crowding the default view.
6. Keep one command model. CLI, HTTP, and viewer actions dispatch the same backend commands.
7. Separate reading from auditing. In each report category, lead with concise evidence-bounded synthesis and keep that category's unchanged trail one disclosure away.
8. Make provenance structural. Ticket-owned information and agent-owned information live in explicit Ticket and Agent sections; Combined and branch reports are selected, never visually blended.
9. Treat integrations as compositions. Presets accelerate setup, but custom integrations use the same field matrix and command contract.
10. Make synthesis intentional. Ordinary checkout, commit, merge, and rebase events mark the affected ticket as changed without calling a model; the user starts synthesis from the ticket state hub when a new summary is useful.
11. Make shared truth explicit. Render local ticket data immediately, synchronize remote Bunker state and Git refs independently, and name synthesis age, commits since, preserved state, and the next action whenever work stops.
12. Keep the coworker in the ticket layer. One private per-user/per-ticket session may explain and propose gated child-report synthesis updates; it never edits source or Combined directly.
13. Make it one application. One compiled interface opens to Bunkers, then Projects, then attached repositories and tickets; Tauri loads it directly and browser fallback serves the same files. Keep the Python API/action registry as the behavior boundary; CLI automation remains an optional client.
14. Treat Project as a recursive work scope, not a repository synonym. A Project may contain subprojects and attach zero or more repositories.
15. Keep discovery separate from authority. Cloning code never joins a Bunker; authenticated invitation or explicit local creation does.
16. Configure connectivity at its real scope. A Bunker alone owns its optional remote service relationship and allowed Git repository registry. Owners expand each Bunker repository and toggle its availability to Projects. Projects offer Add local repository: a folder's origin automatically selects the enabled binding, without asking members to configure remotes. Linking a board syncs its tickets automatically, with an explicit retry if sync fails. Use Save for shared configuration and Add local repository for a device-local path, not Publish. Shared Bunker creation requires service-administrator authority; Project creation requires Bunker ownership. Local creation grants no authority on a server.
17. Keep operating systems outside the domain. Linux and Windows select thin
    machine-service implementations for paths, processes, credentials, dialogs,
    and packaging; WSL is a Linux runtime location, never a Bunker type.

## Accessibility & Inclusion

Target WCAG 2.2 AA contrast and interaction behavior. Support keyboard navigation,
visible focus, reduced motion, zoom, narrow screens, long branch names, and report
content without horizontal page overflow. Never rely on color alone for state.
