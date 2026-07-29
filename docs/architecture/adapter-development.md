# Adapter development

Universal RPA adapters are synchronous Python implementations of
`AutomationAdapter`. They are administrator-installed trusted code. A workflow
can select only registered namespaced capabilities; it cannot supply or execute
Python, shell code, packages, or entry points.

An external package registers one factory through standard package metadata:

```toml
[project.entry-points."universal_rpa.adapters"]
web = "company_rpa_web:create_adapter"
```

Discovery reads installed entry points in deterministic name order and calls
each selected factory once. It never installs a package or performs network
discovery. The factory returns one adapter whose `adapter_id` matches its
immutable descriptor.

Each adapter owns schema validation for its actions, conditions, assertions,
and its own `TargetSpec.payload`. Capability names use
`<adapter_id>.<local_name>`. Cancellation must be checked before UI, native, or
other externally visible work. Exceptions crossing the boundary must become a
common `ErrorCode` and a safe message that excludes raw exception text and
secrets.

The IDs `web`, `http`, `mail`, and `fileops` are reserved for future B/C
implementations. Those implementations are not included in M1. The M1 registry
contract registers deterministic in-memory fakes under those four IDs without
importing Playwright, an HTTP client, a mail client, or a filesystem
implementation.

Descriptors declare the complete retry and verification policy. Workflow JSON
cannot add idempotency, retryable errors, or assertion compatibility. A target
capture preview, when supported, stays in memory; the adapter does not persist
it. Adapter-specific target regions and sensitive regions belong only inside
each candidate's `TargetSpec.payload`.
