# File-backed memory mutations are single-process transactions

Sage keeps JSON and JSONL memory artifacts inspectable, but every mutable file
document must now commit through the transactional persistence module: one
resolved-path lock covers read, mutation, serialization, `fsync`, and atomic
replacement. We chose this over an immediate SQLite-only migration because the
human-readable artifacts are part of the current demo and debugging workflow;
if Sage runs multiple worker processes or hosts, the file seam must be replaced
with SQLite or another store that provides cross-process transactions.
