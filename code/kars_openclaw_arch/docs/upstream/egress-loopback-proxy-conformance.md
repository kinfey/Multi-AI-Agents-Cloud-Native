# Upstream issue: Egress conformance corpus cannot verify a pod-local loopback egress proxy

- **Local finding id:** RT-2026-02
- **Severity:** Medium (control verified sound; residual risk is measurement/observability, not escape)
- **Status:** Accepted (compensated by `scripts/verify-egress.sh`; upstream fix requested)
- **Component:** KARS conformance runner (`egress-known-bad` corpus) + sandbox egress data plane (`egress-guard` initContainer, loopback FQDN proxy on `:8444`)
- **KARS version:** v0.1.25

## Summary

The `egress-known-bad` conformance corpus runs in its **own pod**, separate from the
sandbox pod. The sandbox's egress enforcement is entirely **pod-local**: an `egress-guard`
initContainer programs iptables so that the agent's traffic is transparently redirected to a
forward proxy bound to **loopback only** (`127.0.0.1:8444`). A pod on a different network
namespace can never reach that loopback listener and is not subject to the sandbox's iptables
rules, so the corpus records transport failures ("operation timed out") for every case. Those
failures are scored as `Blocked`, but they are **unverified transport errors**, not evidence
that egress is (or is not) confined. The suite therefore cannot actually verify the control it
is meant to test.

## Impact

- Egress conformance produces a non-actionable signal: 0/6 cases "pass" via transport timeout,
  indistinguishable from a genuine block or a genuine escape.
- Operators cannot use the egress suite as a release gate, because it neither confirms nor
  refutes egress confinement.

## The control the runner cannot observe (verified out-of-band)

Direct pod introspection (2026-07-30) confirms egress **is** correctly confined for the agent:

1. `egress-guard` initContainer (runs with `NET_ADMIN`/`NET_RAW` during init only) installs:
   ```
   iptables -A OUTPUT -m owner --uid-owner 1000 -o lo -j ACCEPT
   iptables -A OUTPUT -m owner --uid-owner 1000 -p udp --dport 53 -j ACCEPT
   iptables -A OUTPUT -m owner --uid-owner 1000 -p tcp --dport 53 -j ACCEPT
   iptables -A OUTPUT -m owner --uid-owner 1000 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
   iptables -A OUTPUT -m owner --uid-owner 1000 -j DROP
   iptables -t nat -A OUTPUT -m owner --uid-owner 1000 ! -o lo -p tcp --dport 80  -j REDIRECT --to-port 8444
   iptables -t nat -A OUTPUT -m owner --uid-owner 1000 ! -o lo -p tcp --dport 443 -j REDIRECT --to-port 8444
   ```
2. The `openclaw` agent container runs as **UID 1000** — non-root, `allowPrivilegeEscalation:
   false`, no added capabilities — so every 80/443 connection it opens is force-routed through
   the `:8444` FQDN proxy, and it cannot change UID to escape the owner-matched rules.
3. The `inference-router` forward proxy runs `EGRESS_MODE=strict` against the FQDN allowlist
   ConfigMap `karssandbox-<sandbox>-egress-allowlist`, which resolves to exactly the sandbox's
   `allowedEndpoints`.

Note: the rendered `sandbox-policy` NetworkPolicy allows `0.0.0.0/0:443` (minus RFC1918). That
is only a coarse L3/L4 backstop; Kubernetes NetworkPolicy cannot express FQDN allowlists, so the
actual per-host enforcement is the transparent proxy above, not the NetworkPolicy.

## Requested upstream mitigation (any of)

1. **Co-locate the egress probe with the sandbox** (ephemeral container / sidecar in the sandbox
   pod, or a runner mode that shares the sandbox's network namespace) so the probe traverses the
   same `egress-guard` iptables path and loopback proxy it is meant to test.
2. **Expose a testable egress hook**: have the forward proxy emit a verifiable decision record
   (allowed/blocked per host) that the runner can assert against, instead of relying on raw
   transport reachability from a foreign pod.
3. **Score transport-unreachable egress cases as `Inconclusive`, not `Blocked`**, so a timeout is
   never mistaken for a verified control.

## Repo-side compensating control (already in place)

- `scripts/verify-egress.sh` deterministically asserts the invariants above against the live
  sandbox pod (openclaw UID 1000; egress-guard default-DROP + 80/443 redirect to `:8444`; router
  `EGRESS_MODE=strict`; non-wildcard FQDN allowlist). It **passes today**, giving a repeatable
  green signal that egress is confined independent of the broken cross-pod conformance path. Run
  it as the egress release gate until the upstream runner can verify the control itself.

## References

- `docs/red-team.md` — "KARS v0.1.25 compatibility" section (egress caveat).
- `scripts/verify-egress.sh` — deterministic egress verification.
- `reports/redteam-20260730-044845.json` — finding `RT-2026-02`.
