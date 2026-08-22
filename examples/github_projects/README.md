# GitHub Projects example

This wrapper prints one GitHub Issue and its Project status as TicketBunker's
provider-neutral JSON. It is the copy/paste form of the packaged `tb github`
transport and adds no third-party Python dependency.

```sh
export GITHUB_ACCESS_TOKEN=...  # needs read access to the repo and Project
python3 examples/github_projects/import_issue.py \
  https://github.com/OWNER/REPO/issues/123 --project 8 \
  | tb ticket --from-json -
```

For a public issue without Project metadata, the token is optional. The
adapter preserves the issue URL, source timestamps, labels, assignment,
milestone, comments, and Project identity/status in provider-neutral metadata
plus the raw GitHub audit fields in `ticket.json`; TicketBunker
maps only lifecycle (`Todo`, `In Progress`, `Done`) into its own status.

The JSON boundary is intentionally boring. A different tracker only needs to
produce the same object and can use the identical `tb ticket --from-json -`
command; flags, the editor template, and `--from FILE` remain available. For
idempotent refreshes use `tb github ISSUE` or `tb github --project NUMBER
--project-owner OWNER`; those commands upsert through the shared ticket gate.
