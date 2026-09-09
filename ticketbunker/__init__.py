"""TicketBunker — an intermediate context layer between agile trackers and git.

Harness-neutral core: stage contracts, execution ledger, deterministic git
bootstrap stages, session-context composition (tb.md), and report tooling.
Stdlib only, by design: the core must be exceedingly simple and diagnosable,
and deployable inside any security boundary.
"""

__version__ = "0.6.4"
