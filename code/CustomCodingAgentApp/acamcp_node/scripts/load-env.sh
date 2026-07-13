#!/usr/bin/env bash

# Load KEY=VALUE pairs from an env file into the environment.
load_env_file() {
  local env_file="${1:-.env}"

  if [ ! -f "$env_file" ]; then
    return 0
  fi

  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
}
