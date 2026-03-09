#!/usr/bin/env bash
# egress-monitor.sh — Egress connection logging
#
# Corresponds to the nftables egress logging in ryoooo/microvm-openclaw.nix:
#
#   networking.nftables.tables.microvm-egress = {
#     family = "inet";
#     content = ''
#       chain forward {
#         type filter hook forward priority 10; policy accept;
#         iifname "microbr" ct state new log prefix "microvm-egress: " accept
#       }
#     '';
#   };
#
# Docker version: install equivalent nftables/iptables rules on the host for the podcast-net bridge.
# Must be run as root after docker-compose up.
#
# Usage:
#   sudo bash security/egress-monitor.sh setup    # install egress logging rules
#   sudo bash security/egress-monitor.sh teardown # remove rules
#   sudo bash security/egress-monitor.sh watch    # live monitoring (journalctl grep)
#   sudo bash security/egress-monitor.sh status   # show current rules

set -euo pipefail

LOG_PREFIX="podcast-egress: "     # syslog prefix, matches microvm-egress:
TABLE="podcast_egress"
CHAIN="podcast_fwd"
NETWORK_NAME="ai-podcast-v2_podcast-net"   # docker network name (from docker network ls)

# ── Utility functions ────────────────────────────────────────────
get_bridge_iface() {
  # Try to get exact interface name from docker network inspect
  local net_id
  net_id=$(docker network inspect "$NETWORK_NAME" --format '{{.Id}}' 2>/dev/null || \
           docker network ls --filter name=podcast-net -q 2>/dev/null | head -1)
  if [ -n "$net_id" ]; then
    echo "br-${net_id:0:12}"
  else
    # fallback: scan existing br- interfaces
    ip link show | grep -oP 'br-[a-f0-9]{12}' | head -1 || echo "docker0"
  fi
}

cmd_setup() {
  local iface
  iface=$(get_bridge_iface)
  echo "[egress-monitor] Target bridge interface: $iface"

  if command -v nft &>/dev/null; then
    echo "[egress-monitor] Using nftables mode..."

    # Idempotent: delete old table first
    nft delete table inet "$TABLE" 2>/dev/null || true

    # Create table + chain (corresponds to type filter hook forward priority 10)
    nft add table inet "$TABLE"
    nft add chain inet "$TABLE" "$CHAIN" \
      '{ type filter hook forward priority 10; policy accept; }'

    # Rule 1: log all new connections from podcast network (core: equivalent to microvm-egress logic)
    nft add rule inet "$TABLE" "$CHAIN" \
      iifname "$iface" ct state new \
      log prefix "\"${LOG_PREFIX}\"" \
      accept

    # Rule 2: block access to host Docker daemon (prevent container escape)
    nft add rule inet "$TABLE" "$CHAIN" \
      iifname "$iface" ip daddr 172.17.0.1 tcp dport 2376 drop

    # Rule 3: block cloud metadata services (AWS/GCP/Azure IMDS, prevent credential theft)
    nft add rule inet "$TABLE" "$CHAIN" \
      iifname "$iface" ip daddr 169.254.169.254 drop

    echo "[egress-monitor] \u2713 nftables rules installed"
    nft list table inet "$TABLE"

  else
    echo "[egress-monitor] nftables not available, using iptables fallback..."

    # iptables LOG rule (equivalent effect)
    iptables -I FORWARD 1 \
      -i "$iface" \
      -m conntrack --ctstate NEW \
      -j LOG --log-prefix "$LOG_PREFIX" --log-level 6 2>/dev/null || \
    iptables -I FORWARD 1 \
      -i "$iface" -m state --state NEW \
      -j LOG --log-prefix "$LOG_PREFIX" 2>/dev/null

    # Block host Docker daemon
    iptables -I FORWARD 2 \
      -i "$iface" -d 172.17.0.1 -p tcp --dport 2376 -j DROP 2>/dev/null || true

    # Block metadata service
    iptables -I FORWARD 3 \
      -i "$iface" -d 169.254.169.254 -j DROP 2>/dev/null || true

    echo "[egress-monitor] \u2713 iptables rules installed (fallback mode)"
  fi

  echo ""
  echo "[egress-monitor] Monitor commands:"
  echo "  sudo journalctl -f | grep '${LOG_PREFIX}'"
  echo "  sudo bash security/egress-monitor.sh watch"
}

cmd_teardown() {
  if command -v nft &>/dev/null; then
    nft delete table inet "$TABLE" 2>/dev/null && \
      echo "[egress-monitor] \u2713 nftables rules removed" || \
      echo "[egress-monitor] rules not found, nothing to remove"
  else
    local iface
    iface=$(get_bridge_iface)
    iptables -D FORWARD -i "$iface" -m conntrack --ctstate NEW \
      -j LOG --log-prefix "$LOG_PREFIX" --log-level 6 2>/dev/null || true
    iptables -D FORWARD -i "$iface" -d 172.17.0.1 -p tcp --dport 2376 -j DROP 2>/dev/null || true
    iptables -D FORWARD -i "$iface" -d 169.254.169.254 -j DROP 2>/dev/null || true
    echo "[egress-monitor] \u2713 iptables rules removed"
  fi
}

cmd_watch() {
  echo "=== Live monitoring OpenClaw egress connections ==="
  echo "=== microvm-egress equivalent mode (Ctrl+C to exit) ==="
  echo ""
  journalctl -f --output=short-monotonic 2>/dev/null | \
    grep --line-buffered "$LOG_PREFIX" | \
    while IFS= read -r line; do
      echo "$(date '+%H:%M:%S') $line"
    done
}

cmd_status() {
  local iface
  iface=$(get_bridge_iface)
  echo "=== Bridge interface: $iface ==="
  ip link show "$iface" 2>/dev/null || echo "(interface not found, run docker compose up first)"
  echo ""
  echo "=== nftables rules ==="
  if command -v nft &>/dev/null; then
    nft list table inet "$TABLE" 2>/dev/null || echo "(rules not installed)"
  fi
  echo ""
  echo "=== iptables FORWARD chain ==="
  iptables -L FORWARD -n -v 2>/dev/null | grep -E "($LOG_PREFIX|169.254|2376)" || echo "(no matching rules)"
}

case "${1:-help}" in
  setup)    cmd_setup ;;
  teardown) cmd_teardown ;;
  watch)    cmd_watch ;;
  status)   cmd_status ;;
  *)
    echo "Usage: sudo bash security/egress-monitor.sh <setup|teardown|watch|status>"
    echo ""
    echo "  setup    \u2014 install nftables/iptables egress logging rules (microvm-egress equivalent)"
    echo "  teardown \u2014 remove rules"
    echo "  watch    \u2014 live monitoring of egress connections (journalctl grep)"
    echo "  status   \u2014 show current rule status"
    ;;
esac
