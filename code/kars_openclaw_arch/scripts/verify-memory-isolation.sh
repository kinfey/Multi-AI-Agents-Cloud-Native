#!/bin/sh
# Deterministically verify the memory-isolation control that the v0.1.25
# conformance runner scores incorrectly.
#
# Why this exists
# ---------------
# The `memory-isolation` corpus issues MemoryReads with foreign scopes
# (other-sandbox, tenant:other-tenant, system, *, ../other) and expects a
# `Blocked` decision. The inference-router instead PINS the memory store: every
# MemoryRead is proxied to the single bound store/scope regardless of the
# caller-supplied scope, and Foundry partitions data per scope, so a foreign
# scope simply returns an empty HTTP 200. The runner scores that safe empty-200
# as `Allowed` (it asserts a blocked *decision*), producing a false
# DecisionMismatch (finding RT-2026-08). The case record alone (Allowed, empty
# reason) cannot prove no data crossed the boundary, so isolation must be proven
# from the pinned binding: if the caller cannot select any store but its own,
# cross-store/cross-tenant reads are impossible by construction.
#
# This script asserts that store-pin invariant and fails (nonzero) on drift.
#
# Usage: scripts/verify-memory-isolation.sh [sandbox-namespace] [sandbox-name] [karsmemory-namespace]
#   defaults: kars-media-claw-agent  media-claw-agent  finance-media
set -eu

ns=${1:-kars-media-claw-agent}
sandbox=${2:-media-claw-agent}
mem_ns=${3:-finance-media}
selector="kars.azure.com/component=sandbox"

fail=0
ok()  { printf '  [ok]   %s\n' "$1"; }
bad() { printf '  [FAIL] %s\n' "$1"; fail=1; }

echo "Verifying memory-isolation (store pin) for sandbox '$sandbox'"

# 1. Locate the KarsMemory bound to this sandbox and confirm it is Ready with a
#    single, non-wildcard store/scope.
mem=$(kubectl get karsmemory -n "$mem_ns" -o json 2>/dev/null | jq -r --arg s "$sandbox" '
    .items[] | select(.spec.sandboxRef.name == $s)
    | {name:.metadata.name, store:.spec.storeName, scope:.spec.scope,
       ready:([.status.conditions[]?|select(.type=="Ready")|.status]|first // .status.phase // "")}
    | @json' | head -1)
if [ -z "$mem" ]; then
    echo "error: no KarsMemory with sandboxRef.name=$sandbox in namespace $mem_ns" >&2
    exit 2
fi
mem_name=$(printf '%s' "$mem" | jq -r '.name')
cr_store=$(printf '%s' "$mem" | jq -r '.store')
cr_scope=$(printf '%s' "$mem" | jq -r '.scope')
cr_ready=$(printf '%s' "$mem" | jq -r '.ready')

[ "$cr_ready" = "True" ] || [ "$cr_ready" = "Ready" ] \
    && ok "KarsMemory '$mem_name' is Ready" \
    || bad "KarsMemory '$mem_name' is not Ready (status='$cr_ready')"

case "$cr_store" in ""|"null") bad "KarsMemory has no storeName";; *) ok "KarsMemory pins store '$cr_store'";; esac
case "$cr_scope" in
    ""|"null"|"*") bad "KarsMemory scope is empty or wildcard ('$cr_scope') — not a single pinned scope";;
    *) ok "KarsMemory pins scope '$cr_scope'";;
esac

# 2. The router's mounted binding must pin exactly that store/scope for THIS
#    sandbox, so the caller cannot address any other store.
binding_cm="karsmemory-${mem_name}-binding"
if b=$(kubectl get configmap "$binding_cm" -n "$ns" -o jsonpath='{.data.binding\.json}' 2>/dev/null) && [ -n "$b" ]; then
    b_store=$(printf '%s' "$b" | jq -r '.storeName // ""')
    b_scope=$(printf '%s' "$b" | jq -r '.scope // ""')
    b_ref=$(printf '%s' "$b" | jq -r '.sandboxRef.name // ""')
    [ "$b_store" = "$cr_store" ] && ok "router binding pins the same store '$b_store'" \
        || bad "router binding store '$b_store' != KarsMemory store '$cr_store'"
    [ "$b_scope" = "$cr_scope" ] && ok "router binding pins the same scope '$b_scope'" \
        || bad "router binding scope '$b_scope' != KarsMemory scope '$cr_scope'"
    [ "$b_ref" = "$sandbox" ] && ok "router binding sandboxRef is this sandbox ('$b_ref')" \
        || bad "router binding sandboxRef '$b_ref' != sandbox '$sandbox'"
else
    bad "router memory binding ConfigMap '$binding_cm' not found or empty in namespace $ns"
fi

# 3. The router consumes a single-binding pin directory (caller cannot select a store).
pod_json=$(kubectl get pod -n "$ns" -l "$selector" -o json 2>/dev/null)
[ "$(printf '%s' "$pod_json" | jq '.items | length')" -gt 0 ] || { echo "error: no sandbox pod found" >&2; exit 2; }
if printf '%s' "$pod_json" | jq -e '.items[0].spec.containers[] | select(.name=="inference-router") | (.env // [])[] | select(.name=="MEMORY_BINDING_DIR")' >/dev/null; then
    ok "inference-router pins memory via MEMORY_BINDING_DIR (single binding; caller cannot choose a store)"
else
    bad "inference-router has no MEMORY_BINDING_DIR — store pin not enforced at the router"
fi

echo
if [ "$fail" -eq 0 ]; then
    echo "PASS: every MemoryRead is pinned to store '$cr_store' scope '$cr_scope'; cross-store/cross-tenant reads are impossible by construction (foreign scopes return an empty partition)."
    exit 0
fi
echo "FAIL: memory store-pin drift detected — do not treat cross-scope reads as isolated." >&2
exit 1
