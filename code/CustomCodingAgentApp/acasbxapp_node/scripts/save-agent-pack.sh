#!/usr/bin/env bash
set -euo pipefail

WORKSPACES_ROOT="${OPENCLAW_WORKSPACES_ROOT:-/state/openclaw/workspaces}"
ARTIFACTS_ROOT="${OPENCLAW_ARTIFACTS_ROOT:-/state/openclaw/artifacts}"
MAX_BYTES="${OPENCLAW_ARTIFACT_MAX_BYTES:-104857600}"
PUBLIC_BASE_URL="${OPENCLAW_GATEWAY_URL:-}"

for command_name in find zip; do
  command -v "$command_name" >/dev/null || {
    echo "Required command is unavailable: $command_name" >&2
    exit 1
  }
done

case "$MAX_BYTES" in
  ''|*[!0-9]*)
    echo "OPENCLAW_ARTIFACT_MAX_BYTES must be a positive integer" >&2
    exit 1
    ;;
esac

if [ "$MAX_BYTES" -le 0 ]; then
  echo "OPENCLAW_ARTIFACT_MAX_BYTES must be a positive integer" >&2
  exit 1
fi

artifact_id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
mkdir -p "$ARTIFACTS_ROOT"
chmod 700 "$ARTIFACTS_ROOT"

staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/save-agent.XXXXXX")"
archive_tmp="$ARTIFACTS_ROOT/.${artifact_id}.zip.tmp"
archive_path="$ARTIFACTS_ROOT/${artifact_id}.zip"
trap 'rm -rf "$staging_dir" "$archive_tmp"' EXIT

total_bytes=0
for agent_id in coding-agent testing-agent; do
  source_dir="$WORKSPACES_ROOT/$agent_id"
  if [ ! -d "$source_dir" ]; then
    echo "Missing agent workspace: $source_dir" >&2
    exit 1
  fi
  if find "$source_dir" -type l -print -quit | grep -q .; then
    echo "Symbolic links are not allowed in $agent_id workspace" >&2
    exit 1
  fi

  destination_dir="$staging_dir/$agent_id"
  mkdir -p "$destination_dir"
  while IFS= read -r -d '' source_file; do
    relative_path="${source_file#"$source_dir"/}"
    case "/$relative_path" in
      */.env|*/.env.*|*/id_rsa|*/id_ed25519|*/.git-credentials|*/.azure/*|*/.ssh/*|*/credentials.json)
        continue
        ;;
    esac

    file_bytes="$(wc -c < "$source_file" | tr -d ' ')"
    total_bytes=$((total_bytes + file_bytes))
    if [ "$total_bytes" -gt "$MAX_BYTES" ]; then
      echo "Artifact exceeds OPENCLAW_ARTIFACT_MAX_BYTES" >&2
      exit 1
    fi

    mkdir -p "$destination_dir/$(dirname "$relative_path")"
    cp -p "$source_file" "$destination_dir/$relative_path"
  done < <(find "$source_dir" -type f -print0)
done

(
  cd "$staging_dir"
  zip -q -r "$archive_tmp" coding-agent testing-agent
)
chmod 600 "$archive_tmp"
mv "$archive_tmp" "$archive_path"

download_path="/api/saveagent/artifacts/${artifact_id}.zip"
if [ -n "$PUBLIC_BASE_URL" ]; then
  download_url="${PUBLIC_BASE_URL%/}${download_path}"
else
  download_url="$download_path"
fi

printf '{"artifactId":"%s","archive":"%s","downloadUrl":"%s","bytes":%s}\n' \
  "$artifact_id" "$archive_path" "$download_url" "$total_bytes"