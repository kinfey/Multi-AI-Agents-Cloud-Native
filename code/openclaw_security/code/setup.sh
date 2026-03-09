#!/usr/bin/env bash
# setup.sh — One-command deployment (hardened security edition)
set -euo pipefail

GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; BOLD="\033[1m"; RESET="\033[0m"
info()    { echo -e "${GREEN}[INFO]${RESET}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
section() { echo -e "\n${BOLD}━━━  $*  ━━━${RESET}"; }

section "Step 0: Prerequisites"
command -v docker >/dev/null 2>&1 || { error "docker not found"; exit 1; }
docker compose version >/dev/null 2>&1 || { error "docker compose v2 is required"; exit 1; }
info "Docker OK"

section "Step 1: Generate .env"
if [ ! -f .env ]; then
  TOKEN=$(openssl rand -hex 24 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(24))")
  sed "s/change_me_to_a_random_secret_string/${TOKEN}/" .env.example > .env
  info ".env created (OPENCLAW_TOKEN used as fallback; primary token rotated by secrets-init)"
else
  warn ".env already exists, skipping"
fi

SERPAPI_KEY=$(grep SERPAPI_KEY .env | cut -d= -f2 | tr -d ' ')
if [ -z "$SERPAPI_KEY" ] || [ "$SERPAPI_KEY" = "your_serpapi_key_here" ]; then
  error "SERPAPI_KEY not configured! Edit .env: https://serpapi.com/manage-api-key"
  exit 1
fi
info "SERPAPI_KEY configured ✓"

section "Step 2: Directory permissions"
mkdir -p config workspace output security
chmod 700 config       # openclaw.json contains sensitive configuration
chmod 755 security
chmod +x security/egress-monitor.sh security/secrets-init.sh 2>/dev/null || true
info "Directory permissions set"

section "Step 3: Pull images (with retry)"
# Auto-retry on TLS handshake timeout (up to 5 attempts, increasing backoff)
pull_with_retry() {
  local image="$1"
  local max=5
  for i in $(seq 1 $max); do
    info "Pulling $image (attempt $i/$max)..."
    docker pull "$image" && return 0
    warn "Pull failed, retrying in ${i}0 seconds..."
    sleep $((i * 10))
  done
  error "Failed to pull $image (tried $max times)"
  return 1
}

# Pull sequentially to avoid concurrent TLS connection contention
pull_with_retry alpine:3.19
pull_with_retry mvance/unbound:latest
pull_with_retry ollama/ollama:latest

section "Step 4: Start services"
info "Starting all services..."
docker compose up -d --build

section "Step 5: Wait for services to be ready"
info "Waiting for secrets-init to generate token..."
for i in $(seq 1 20); do
  docker compose exec -T secrets-init test -f /run/secrets/gateway-token 2>/dev/null && {
    info "secrets-init ready ✓ (token generated)"
    break
  }
  echo -n "."; sleep 2
done
echo ""

info "Waiting for dns-audit (Unbound) to be ready..."
for i in $(seq 1 20); do
  docker compose exec -T dns-audit dig @127.0.0.1 +short cloudflare.com >/dev/null 2>&1 && {
    info "dns-audit ready ✓"
    break
  }
  echo -n "."; sleep 2
done
echo ""

info "Waiting for Ollama to be ready..."
for i in $(seq 1 40); do
  docker compose exec -T ollama curl -fs http://localhost:11434/api/tags >/dev/null 2>&1 && {
    info "Ollama ready ✓"
    break
  }
  echo -n "."; sleep 3
done
echo ""

section "Step 6: Pull model"
docker compose run --rm model-init

section "Step 7: Install host nftables egress logging"
echo ""
warn "Recommended: install host nftables egress logging rules (equivalent to microvm-egress):"
echo "  sudo bash security/egress-monitor.sh setup"
echo ""
echo "  After installation, monitor all OpenClaw outbound connections in real time:"
echo "  sudo bash security/egress-monitor.sh watch"
echo ""

section "Done!"
echo ""
echo -e "${BOLD}Usage:${RESET}"
echo ""
echo "  # Single run (fully automatic, no user input required)"
echo "  docker compose run --rm podcast-app"
echo ""
echo "  # Scheduled mode (every 6 hours)"
echo "  docker compose run -d -e SCHEDULE_HOURS=6 --name scheduler podcast-app"
echo ""
echo "  # View current trending topics only"
echo "  docker compose run --rm podcast-app python auto_run.py --scout-only"
echo ""
echo -e "${BOLD}Security monitoring:${RESET}"
echo ""
echo "  # DNS query log (Unbound audit)"
echo "  docker logs -f dns-audit"
echo ""
echo "  # secrets directory access audit"
echo "  docker logs -f secrets-init"
echo ""
echo "  # Egress connection log (after installation)"
echo "  sudo bash security/egress-monitor.sh watch"
echo ""
echo -e "  Podcast output directory: ${BOLD}./output/${RESET}"
