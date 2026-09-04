"""Reference TicketBunker service (DESIGN.md §3.20).

One stdlib-only process that owns one SQLite file and implements the versioned
``/api/v1`` contract in ``docs/remote-service-contract.md``. Teams run it on a
machine they control; larger deployments implement the same contract on their
own infrastructure. Desktop clients never see this database.
"""
