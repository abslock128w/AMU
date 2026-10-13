# State validation, transactions and recovery

AMU now checks the nested shapes of saved maps, contracts, phase/node state,
memory, answers, refresh records, sync records and navigation. Older partial
records can still be inspected, but missing fields do not become successful
lifecycle evidence. Generated records still require their complete workflow
schema, source proof and successful test/build results.

Malformed saved JSON produces `state_unreadable` with CLI exit 2. Defaults apply
only to missing files. Existing corrupt data stays on disk; restore the affected
file from a known backup or repair the reported field, then rerun the command.
AMU does not silently clear a corrupt contract or reset a corrupt retry budget.

If Git cannot enumerate changed files, `amu check`, `amu sweep`, and `amu done`
stop with CLI exit 2 (`git_change_enumeration_failed` in JSON) instead of
reporting a clean worktree. The diagnostic names the failing Git operation and
asks you to check Git access, the repository, or the approved base ref before
retrying. A failed check attempt remains nonpassing.

JSON reads are limited to 16 MiB and reject ambiguous duplicate keys, nonfinite
numbers, unsafe links and nonregular files. Managed state paths stay inside their
directory. State directories and lock files must be owned by the current user;
shared writable directories and symlinked state directories are rejected.
Repository-path aliases remain supported. New files and persistent lock files
use private permissions.

Writes replace through a pinned directory descriptor, preserving the previous
file on replacement failure. Transactions hold the same persistent lock inode
for their entire read/update/write operation. Locks do not disappear between
operations. Errors raised by a transaction's operation retain their original
meaning, which keeps documentation save/recovery behavior intact.

## Handoffs and checker budgets

All harness handoffs reserve their next ID in `run/sequence.json` under one run
lock. The reservation is saved before the artifact. An interrupted attempt may
leave a gap; a later handoff never reuses that identifier or overwrites an older
role's decision.

Checker counters are stored per task under `run/budgets/`, using a hash of the
task identity for the filename. The former `run/budget.json` is migrated before
another task replaces it and remains a compatibility mirror. Each check reloads,
spends and persists its allowance under the same lock. Switching tasks does not
restore an exhausted allowance. Approving/resetting a task resets only that task.
An artifact-write failure after a spend leaves that spend recorded.

Workspace, memory, unknown-answer, refresh/check state and navigation updates use
transactional helpers. This does not make every multi-file workflow one database
transaction: documentation saves retain their recovery journal, and coordinating
whole approval workflows across artifacts remains separate service-layer work.

Descriptor access and process locks are supported on the current macOS/Linux
hosts. Hosts without those facilities fail explicitly instead of providing only
thread-level protection. The automated tests include real concurrent processes,
stale instances, interruption, malformed counters, directory/file swaps and
failed replacement recovery.
