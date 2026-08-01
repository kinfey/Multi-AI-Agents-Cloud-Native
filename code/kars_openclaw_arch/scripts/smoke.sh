#!/bin/sh
set -eu

python -m pytest
python -m compileall -q agents

for url in "${MEDIA_APP_URI:-http://localhost:8000}" "${REDTEAM_APP_URI:-http://localhost:8001}"
do
    curl --fail --silent --show-error "$url/health" >/dev/null
done

echo "Smoke checks passed"
