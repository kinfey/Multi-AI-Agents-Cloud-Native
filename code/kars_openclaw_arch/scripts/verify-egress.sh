#!/bin/sh
# Deterministically verify the sandbox egress control that the v0.1.25 conformance
# runner cannot observe from a separate pod.
#
# Why this exists
# ---------------
# The `egress-known-bad` conformance corpus executes in its own pod and tries to
# reach the sandbox's forward proxy, which binds loopback-only (127.0.0.1:8444)
# inside the sandbox pod. A separate pod can never reach that loopback listener,
# so the suite records transport failures ("operation timed out") that prove
# nothing about whether egress actually escapes (finding RT-2026-02). The real
# egress control is pod-local and therefore must be verified by pod introspection.
#
# What actually enforces egress (verified 2026-07-30)
# ---------------------------------------------------
# 1. The `egress-guard` initContainer installs iptables OUTPUT rules scoped to
#    `--uid-owner 1000`: DNS + loopback + ESTABLISHED are accepted, EVERYTHING
#    else is DROPPED, and tcp/80 + tcp/443 are transparently REDIRECTED to the
#    loopback FQDN proxy on :8444.
# 2. The `openclaw` agent container runs as UID 1000, so every outbound 80/443
#    connection it opens is force-routed through that FQDN proxy; it is non-root,
#    cannot escalate, and has no CAP_SETUID, so it cannot move off UID 1000 to
#    dodge the owner-matched rules.
# 3. The `inference-router` proxy runs EGRESS_MODE=strict and enforces the FQDN
#    allowlist mounted at EGRESS_ALLOWLIST_DIR.
#
# This script asserts each of those invariants and fails (nonzero) on any drift,
# giving a repeatable green signal that egress is confined even though the
# conformance runner cannot verify it cross-pod.
#
# Usage: scripts/verify-egress.sh [sandbox-namespace] [sandbox-name]
#   defaults: kars-media-claw-agent  media-claw-agent
set -eu

ns=${1:-kars-media-claw-agent}
sandbox=${2:-media-claw-agent}
selector="kars.azure.com/component=sandbox"
allowlist_cm="karssandbox-${sandbox}-egress-allowlist"

fail=0
note() { printf '  %s %s\n' "$1" "$2"; }
ok()   { note "[ok]  " "$1"; }
bad()  { note "[FAIL]" "$1"; fail=1; }

echo "Verifying egress control for sandbox '$sandbox' in namespace '$ns'"

pod_json=$(kubectl get pod -n "$ns" -l "$selector" -o json)
count=$(printf '%s' "$pod_json" | jq '.items | length')
if [ "$count" -eq 0 ]; then
    echo "error: no sandbox pod found (namespace '$ns', selector '$selector')" >&2
    exit 2
fi

# 1. openclaw runs as UID 1000 (covered by the uid-owner iptables rules).
uid=$(printf '%s' "$pod_json" | jq -r '
    .items[0] as $p
    | ($p.spec.containers[] | select(.name=="openclaw") | .securityContext.runAsUser)
      // $p.spec.securityContext.runAsUser // empty')
if [ "$uid" = "1000" ]; then
    ok "openclaw runs as UID 1000 (matched by egress-guard owner rules)"
else
    bad "openclaw runs as UID '$uid', not 1000 — egress-guard uid-owner rules would NOT cover it"
fi

# 2. openclaw cannot escalate or change UID to escape the owner match.
sc=$(printf '%s' "$pod_json" | jq -r '.items[0].spec.containers[] | select(.name=="openclaw") | .securityContext // {}')
[ "$(printf '%s' "$sc" | jq -r '.allowPrivilegeEscalation')" = "false" ] \
    && ok "openclaw allowPrivilegeEscalation=false" \
    || bad "openclaw allowPrivilegeEscalation is not false"
if printf '%s' "$sc" | jq -e '(.capabilities.add // []) | length == 0' >/dev/null; then
    ok "openclaw adds no Linux capabilities"
else
    bad "openclaw adds capabilities — could enable a UID change to bypass egress"
fi

# 3. egress-guard initContainer installs default-DROP + transparent redirect for UID 1000.
guard=$(printf '%s' "$pod_json" | jq -r '.items[0].spec.initContainers[]? | select(.name=="egress-guard") | (.command // [] | join(" ")) + " " + (.args // [] | join(" "))')
if [ -z "$guard" ]; then
    bad "no egress-guard initContainer found"
else
    printf '%s' "$guard" | grep -Eq -- '--uid-owner 1000 -j DROP' \
        && ok "egress-guard default-DROPs non-allowed egress for UID 1000" \
        || bad "egress-guard is missing the UID-1000 default DROP rule"
    printf '%s' "$guard" | grep -Eq -- '--dport 443 -j REDIRECT --to-port 8444' \
        && ok "egress-guard transparently redirects tcp/443 to the :8444 FQDN proxy" \
        || bad "egress-guard does not redirect tcp/443 to :8444"
    printf '%s' "$guard" | grep -Eq -- '--dport 80 -j REDIRECT --to-port 8444' \
        && ok "egress-guard transparently redirects tcp/80 to the :8444 FQDN proxy" \
        || bad "egress-guard does not redirect tcp/80 to :8444"
fi

# 4. inference-router enforces the FQDN allowlist in strict mode.
router_env=$(printf '%s' "$pod_json" | jq -r '.items[0].spec.containers[] | select(.name=="inference-router") | (.env // [])[] | "\(.name)=\(.value)"')
printf '%s\n' "$router_env" | grep -qx 'EGRESS_MODE=strict' \
    && ok "inference-router EGRESS_MODE=strict" \
    || bad "inference-router EGRESS_MODE is not 'strict'"

# 5. FQDN allowlist is present, well-formed, and free of wildcard hosts.
if allow_json=$(kubectl get configmap "$allowlist_cm" -n "$ns" -o jsonpath='{.data.allowlist\.json}' 2>/dev/null) && [ -n "$allow_json" ]; then
    n_ep=$(printf '%s' "$allow_json" | jq '.endpoints | length')
    if [ "$n_ep" -gt 0 ] && ! printf '%s' "$allow_json" | jq -e '.endpoints[] | select(.host | test("[*]"))' >/dev/null; then
        ok "egress allowlist has $n_ep endpoint(s), no wildcard hosts"
        printf '%s' "$allow_json" | jq -r '.endpoints[] | "        - " + .host + ":" + (.port|tostring)'
    else
        bad "egress allowlist is empty or contains a wildcard host"
    fi
else
    bad "egress allowlist ConfigMap '$allowlist_cm' not found or empty"
fi

echo
if [ "$fail" -eq 0 ]; then
    echo "PASS: sandbox egress is confined to the FQDN allowlist via the UID-1000 transparent proxy."
    exit 0
fi
echo "FAIL: egress control drift detected — do not treat egress as confined." >&2
exit 1
