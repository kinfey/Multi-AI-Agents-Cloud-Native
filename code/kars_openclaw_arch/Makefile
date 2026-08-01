# Prefer the project virtualenv when it exists: pytest and ruff are installed
# there, while a bare `python3` on macOS resolves to the Homebrew interpreter
# (currently 3.14) which has neither -- making `make validate-local` fail for
# reasons unrelated to the code under test. Override with `make PYTHON=...`.
PYTHON ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)

MEDIA_CLAW_OPENCLAW_IMAGE ?= local.azurecr.io/media-claw-agent:dev
KARS_OPENCLAW_IMAGE ?= ghcr.io/azure/openclaw-sandbox:v0.1.25

# Egress allowlist + workload-identity targets substituted into
# kars/base/sandboxes.yaml. Hosts derive from the account names, so a new
# environment must supply these two account names. For example:
# make render-kars AZURE_STORAGE_ACCOUNT='<storage-account>' AZURE_FOUNDRY_ACCOUNT='<foundry-account>'
AZURE_STORAGE_ACCOUNT ?=
AZURE_FOUNDRY_ACCOUNT ?=
NEWS_FEED_HOST ?= www.cnbc.com
NEWS_FEED_HOST_2 ?= finance.yahoo.com
AZURE_STORAGE_HOST ?= $(AZURE_STORAGE_ACCOUNT).blob.core.windows.net
AZURE_FOUNDRY_HOST ?= $(AZURE_FOUNDRY_ACCOUNT).services.ai.azure.com
AZURE_SPEECH_HOST ?= $(AZURE_FOUNDRY_ACCOUNT).cognitiveservices.azure.com

.PHONY: install test lint compile validate-local render-kars bicep compose-up compose-down smoke redteam redteam-preflight redteam-suite refresh-media

install:
	$(PYTHON) -m pip install -r agents/media-claw-agent/requirements.txt -r agents/media-app-agent/requirements.txt -r agents/media-testing-agent/requirements.txt pytest ruff

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check agents

compile:
	$(PYTHON) -m compileall -q agents

# The old form piped kustomize into `if grep ...`, so the recipe's exit code
# came from grep alone: a kustomize/envsubst failure (or empty output) was
# reported as a PASS.
#
# The old `grep '$${'` test was also vacuous: envsubst replaces an undefined
# ${VAR} with the EMPTY STRING, so no '${' ever survives. Dropping a variable
# silently rendered `image:` / `- host:` / `- account:` as null -- a manifest
# that greps clean but is broken. So: render to files, assert non-empty at each
# stage, restrict substitution to the known variable set, and then reject any
# substituted key that came out empty.
render-kars:
	@set -eu; \
		export MEDIA_CLAW_OPENCLAW_IMAGE='$(MEDIA_CLAW_OPENCLAW_IMAGE)' \
			KARS_OPENCLAW_IMAGE='$(KARS_OPENCLAW_IMAGE)' \
			AZURE_STORAGE_ACCOUNT='$(AZURE_STORAGE_ACCOUNT)' \
			AZURE_FOUNDRY_ACCOUNT='$(AZURE_FOUNDRY_ACCOUNT)' \
			AZURE_STORAGE_HOST='$(AZURE_STORAGE_HOST)' \
			AZURE_FOUNDRY_HOST='$(AZURE_FOUNDRY_HOST)' \
			AZURE_SPEECH_HOST='$(AZURE_SPEECH_HOST)' \
			NEWS_FEED_HOST='$(NEWS_FEED_HOST)' \
			NEWS_FEED_HOST_2='$(NEWS_FEED_HOST_2)'; \
		raw="$$(mktemp)"; out="$$(mktemp)"; \
		trap 'rm -f "$$raw" "$$out"' EXIT INT TERM; \
		kubectl kustomize kars/overlays/azure > "$$raw"; \
		[ -s "$$raw" ] || { echo "render-kars: kustomize produced no output" >&2; exit 1; }; \
		envsubst '$$MEDIA_CLAW_OPENCLAW_IMAGE $$KARS_OPENCLAW_IMAGE $$AZURE_STORAGE_ACCOUNT $$AZURE_FOUNDRY_ACCOUNT $$AZURE_STORAGE_HOST $$AZURE_FOUNDRY_HOST $$AZURE_SPEECH_HOST $$NEWS_FEED_HOST $$NEWS_FEED_HOST_2' < "$$raw" > "$$out"; \
		[ -s "$$out" ] || { echo "render-kars: envsubst produced no output" >&2; exit 1; }; \
		if grep -Fn '$${' "$$out" >&2; then \
			echo "render-kars: unresolved KARS variables (listed above)" >&2; \
			exit 1; \
		fi; \
		if grep -nE '^[[:space:]]*-?[[:space:]]*(image|host|account):[[:space:]]*$$' "$$out" >&2; then \
			echo "render-kars: empty value after substitution (a variable above is unset)" >&2; \
			exit 1; \
		fi; \
		echo "render-kars: OK ($$(wc -l < "$$out" | tr -d ' ') lines)"

bicep:
	az bicep build --file infra/main.bicep --stdout >/dev/null

validate-local: test lint compile render-kars bicep
	@set -eu; \
		for script in scripts/*.sh agents/media-claw-agent/openclaw-entrypoint.sh; do \
			sh -n "$$script" || { echo "validate-local: syntax error in $$script" >&2; exit 1; }; \
		done; \
		echo "validate-local: OK"

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down

smoke:
	./scripts/smoke.sh

redteam:
	./scripts/run-redteam.sh cloud-all

redteam-preflight:
	./scripts/run-redteam.sh preflight

redteam-suite:
	@test -n "$(SUITE)" || { echo "usage: make redteam-suite SUITE=media-memory-isolation" >&2; exit 2; }
	./scripts/run-redteam.sh cloud-suite "$(SUITE)"

refresh-media:
	@test -n "$(DATE)" || { echo "usage: make refresh-media DATE=YYYY-MM-DD" >&2; exit 2; }
	PYTHONPATH=agents/media-claw-agent/src $(PYTHON) -m media_claw_agent.kubernetes_job --date "$(DATE)"
