# Failure workflows: snapshots, stale results and staged saves

[简体中文](FAILURE_WORKFLOWS.zh-CN.md) · [Overview](../README.en.md)

Three newly written, runnable synthetic examples turn recurring maintenance failure
patterns into small reusable mechanisms. Study or adapt them for documents, preferences
and previews without a game, account, service or private dataset.

## Run the examples

From the repository root:

```shell
python examples/failure_workflows.py
python examples/failure_workflows.py --scenario atomic-replace-error
python examples/failure_workflows.py --scenario refresh-late-result
python examples/failure_workflows.py --scenario staged-local-error
python -m unittest discover -s tests -p test_failure_workflows.py -v
```

The default command prints nine deterministic cases marked `synthetic: true`.
Filesystem demos use a newly owned temporary directory; no network requests occur.
[Reference implementation](../examples/failure_workflows.py) and
[positive/negative tests](../tests/test_failure_workflows.py) live in this repository.
They are importable reference code, not additions to the installed package's stable API;
a pip-only library installation does not include the examples.

| Slice | Flow | Invariant | Useful issue |
| --- | --- | --- | --- |
| JSON snapshot | encode, write a sibling temporary file, replace | encoding failures do not touch disk; failed replacement keeps the old target; cleanup concerns only the owned temporary file | a synthetic lock, leftover temporary file, masking error or platform difference |
| Refresh gate | revision/freshness, request handle, acceptance/failure | only accepted results suppress future ticks; failures allow retry; old A cannot complete new A after A→B→A | a virtual-clock or completion-order counterexample |
| Staged save | remote acknowledgement, local commit, optional restoration | remote/local/recovery results remain separate; an uncertain remote response is not automatically retried | flattened success, hidden failed recovery or duplicate action |

## Atomic snapshots

`write_json_snapshot(destination, value)` fully encodes small JSON before creating a
same-directory temporary file. It never recursively cleans a directory or removes a
sibling file. `SnapshotWriteError` retains `operation_error` and `cleanup_error` separately.
Encoding errors propagate before disk changes; unsupported values, cycles, non-finite
numbers and invalid Unicode have negative cases.

The injected `replace=` seam must honor the `os.replace` contract. There are no default
retries. This is replacement visibility, not a guarantee of power-loss durability,
network-filesystem behavior, permission preservation or multi-writer transactions.
The parent directory must exist; application schema validation is the caller's job.

Lesson: successful normal writes do not establish failure behavior. Check the old target,
sibling files and owned temporary file independently.

## Refresh requests and accepted results

`RefreshGate.request(revision, stale=...)` returns a new opaque local handle, or `None`
when a matching request/result already exists. Only `accept(ticket)` records an accepted
result; `fail(ticket)` permits retry. Copied, foreign and superseded handles cannot finish
the current request.

`refresh-once` completes one fresh and one stale request, then produces zero new requests
for ten further stale ticks. `refresh-late-result` rejects both old completions in an
A→B→A sequence and accepts only the final A.

This is a single-event-loop model, not a thread-safe scheduler. Freshness is supplied by
the caller or a virtual clock. Handles are not serializable cross-process receipts.
An age-only trigger can repeat work every tick; comparing only visible content can
mistake a superseded operation for the current one.

## Independent save outcomes

`save_in_stages(accept_remote, save_local, restore_local)` uses three callbacks.
Remote acknowledgement must be an actual bool; local steps return `None` on success
and raise on failure. Ambiguous values such as `False` are not local success acknowledgements.
The demos use fake in-memory document revisions, not an authorization implementation.

```json
{"remote": "accepted", "local": "failed", "recovery": "restored", "error_type": "OSError", "recovery_error_type": null}
```

This means remote acceptance, failed local commit and restored old local state—not
service unavailability and not complete success. An uncertain remote result is `unknown`;
there is no automatic resend. Real adapters need their own idempotency, reconciliation
and process-crash recovery policies; this example is not a distributed transaction system.

## Contributing useful counterexamples

Use the [issue chooser](https://github.com/SeasonCake/bidking-inference/issues/new/choose)
and include the component, commit/version, OS/runtime, exact command, smallest synthetic
input/event trace, expected invariant and actual result. State whether it repeats and
whether a particular filesystem/platform is required.

For example, a refresh trace can be only
`request A → request B → request A → complete old A`. A long video or real dataset is
not required. Public Windows-release reports should contain only non-sensitive steps
and symptoms; account details, credentials and complete diagnostics use private support.

## Open contribution directions

These are proposals, not implemented modules or runnable commands:

- distinguish reachability, readiness and business completion with synthetic observations;
- make units, rounding and non-compounding single-value display projections explicit;
- separate outcome-only evaluation from complete replay eligibility.

Start with a minimal contract and falsifying control. Check the existing
[evidence-lifecycle case](EVIDENCE_LIFECYCLE.md) for overlapping eligibility work.
Current product code, real protocols/endpoints, core parameters, game tables,
calibration datasets and internal incident text remain excluded.
