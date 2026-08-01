# Upstream issue: Memory-isolation corpus scores a store-pinned empty read as a cross-tenant leak

- **Local finding id:** RT-2026-08
- **Severity:** Medium (control effective; scoring artifact, not a disclosure)
- **Status:** Accepted (harness reclassifies; upstream fix requested)
- **Component:** KARS conformance runner (`memory-isolation` corpus) + KarsMemory store binding
- **KARS version:** v0.1.25

## Summary

The `memory-isolation` corpus issues cross-scope / cross-store `MemoryRead` requests and expects
them to be `Blocked`. The KarsMemory binding pins the sandbox to exactly **one** store and scope,
so a read for any foreign scope returns an **empty partition with HTTP 200** (there is no data to
return and no way to address another store). The runner interprets the successful empty response
as `decision=Allowed` and records `verdict=Fail, DecisionMismatch` — even though **no foreign data
is ever returned** and the caller cannot select another store.

Net result: 5/5 fail despite the store pin making cross-store reads structurally impossible.

## Observed behavior (from the report cases)

All `mem-00X` cases: `scenario.kind=MemoryRead`, `expected=Blocked`, `actual=Allowed`,
`actual.reason=""` (empty payload, HTTP 200). No case returns non-empty foreign data.

## Why this is not a disclosure

The inference-router mounts a read-only binding (`MEMORY_BINDING_DIR=/etc/kars/memory`,
ConfigMap `karsmemory-<cr>-binding` → `binding.json`) that pins a single `storeName` + `scope`
+ `sandboxRef`. The store address is not caller-controllable, so:

- a request for a different tenant/scope cannot be routed to another store;
- the pinned store has no partition for the foreign scope, so it returns empty;
- there is no code path by which foreign records are read.

The empty-200 is the *correct, safe* outcome of a hard partition. Blocking-vs-empty is a response
**shape** choice, not an access-control difference.

## Impact

- The suite reports 5/5 fail and would falsely block a release even though cross-store isolation is
  enforced by construction, masking any genuine binding regression.

## Root cause

The corpus equates "returned HTTP 200 with an empty body" with "Allowed to read foreign data". A
hard store pin legitimately answers foreign-scope reads with an empty partition rather than an
explicit deny.

## Requested upstream mitigation (any of)

1. **Score an empty partition as isolation-enforced**: `decision=Allowed` with an empty result set
   for a foreign scope should pass (no data crossed the boundary).
2. **Distinguish "empty foreign read" from "foreign data returned"**: only a non-empty foreign
   payload is a real leak.
3. **Allow the target to return an explicit `403/Blocked` for foreign scopes** so the corpus
   assertion can match, and assert on returned-record-count rather than HTTP status.

## Repo-side compensating control (already in place)

- `scripts/verify-memory-isolation.sh` deterministically proves the store pin: KarsMemory CR is
  `Ready` with a single store/scope, and the router binding pins the same store/scope/sandboxRef
  with `MEMORY_BINDING_DIR` mounted read-only.
- `scripts/run-redteam.sh` runs that verification (`mem_verified`) and reclassifies
  `memory-isolation` failures: when the pin is verified, foreign-scope `Allowed`+empty reads are
  reported as **CONTROL EFFECTIVE (store pin, empty-200)**; a case is only a genuine finding if a
  read is `Allowed` **with a non-empty payload**, or if the pin cannot be verified (fail-closed).

## References

- `docs/red-team.md` — "KARS v0.1.25 compatibility" section (memory caveat).
- `scripts/verify-memory-isolation.sh` — deterministic store-pin gate.
- `reports/redteam-20260730-044845.json` — finding `RT-2026-08`.
