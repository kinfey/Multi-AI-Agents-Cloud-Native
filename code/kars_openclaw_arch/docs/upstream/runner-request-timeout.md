# Upstream issue: Runner ~5s per-request timeout is scored as a false Blocked/DecisionMismatch

- **Local finding id:** RT-2026-07
- **Severity:** Medium (measurement artifact; not a policy gap)
- **Status:** Accepted (harness reclassifies + re-runs; upstream fix requested)
- **Component:** KARS conformance runner (`ChatCompletion` corpora: prompt-injection, jailbreak)
- **KARS version:** v0.1.25

## Summary

The conformance runner applies an ~5s per-request HTTP timeout to
`…svc:8443/v1/chat/completions`. `gpt-5.5` occasionally exceeds it on adversarial prompts
(observed consistently on `jb-003-roleplay-bypass`, intermittently on `pi-004`/`pi-006`),
surfacing as `actual.reason = "transport error: … operation timed out"`. The runner scores that
transport failure as `Blocked`, which:

- **fails benign controls** (e.g. `pi-006`, whose expected decision is *not* Blocked), and
- is flagged **`DecisionMismatch`** on attack cases because a slow-but-not-blocked response is
  recorded as if Prompt Shields had made a block decision.

Either way the result reflects transport latency, not a policy decision, so it produces false
findings and makes the affected suites non-deterministic across runs.

## Impact

- False `Blocked`/`DecisionMismatch` verdicts pollute the report and can either mask or fabricate
  a security finding depending on the case's expected decision.
- Suites become flaky: the same corpus passes or fails run-to-run purely on model latency.

## Reproduction

Run the prompt-injection / jailbreak corpora against a `gpt-5.5`-backed router; `jb-003` reliably
records `transport error: … operation timed out` at ~5s while other cases complete in ~2.5–3.3s.

## Root cause

The ~5s per-request timeout is hardcoded in the runner binary. There is **no** CLI flag, env var,
or `KarsEval` CRD field to raise it (verified against v0.1.25: the runner container args are only
`--corpus`, `--corpus-label`, `--router-base`, `--output`, and the CRD exposes no timeout field).
So operators cannot give a legitimately slow model enough headroom.

## Requested upstream mitigation (any of)

1. **Make the per-request timeout configurable** via a `--request-timeout` flag / env var (or a
   `KarsEval.spec` field), with a default that accommodates reasoning models.
2. **Score transport failures as `Inconclusive`, not `Blocked`**, so a timeout is never counted as
   a policy decision or a `DecisionMismatch`.
3. **Add a bounded per-case retry** for transport errors before recording a verdict.

## Repo-side compensating control (already in place)

`scripts/run-redteam.sh` now reclassifies per-case results whose `actual.reason` matches
`timed out` / `transport error`:

- They are excluded from the policy-finding count and reported as **INCONCLUSIVE**, not
  `POLICY FINDING`.
- A suite whose only failures are transport timeouts exits nonzero as **INCONCLUSIVE** (not a
  security verdict) and prints the exact `cloud-suite` re-run command(s) to obtain a real verdict.
- Mixed suites still report genuine policy findings, with the transport-timeout cases excluded and
  annotated.

This removes the false Blocked/DecisionMismatch from the harness verdict; re-running the suite (as
the tooling instructs) distinguishes a stable finding from a latency blip until the runner exposes
a timeout knob.

## References

- `docs/red-team.md` — "KARS v0.1.25 compatibility" section (per-request timeout caveat).
- `scripts/run-redteam.sh` — `[5/5]` collection: transport-timeout reclassification + re-run guidance.
- `reports/redteam-20260730-044845.json` — finding `RT-2026-07`.
