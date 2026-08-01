#!/bin/sh
# Publish a normalized red-team report to the red-team Container App's Blob container.
#
# The report container lives in the app's OWN storage account (azd output
# AZURE_STORAGE_ACCOUNT_URL, resource-token account azst<token>), NOT the KARS
# media-pipeline account configured through Makefile AZURE_STORAGE_ACCOUNT.
# Both accounts expose a "redteam" container, so publishing to the wrong one
# leaves the app's /api/reports without the new run. This script always derives
# the correct account from azd to remove that footgun.
#
# Usage: scripts/publish-redteam-report.sh <path-to-report.json> [run-id]
set -eu

get_azd_value() {
    azd env get-value "$1"
}

report_file=${1:-}
if [ -z "$report_file" ] || [ ! -f "$report_file" ]; then
    echo "usage: $0 <path-to-report.json> [run-id]" >&2
    echo "  report file not found: '$report_file'" >&2
    exit 2
fi

# Validate the file is well-formed JSON before uploading; a malformed blob makes
# the app's EvalReport validation fail for the whole /api/reports listing.
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$report_file" 2>/dev/null; then
    echo "error: '$report_file' is not valid JSON" >&2
    exit 3
fi

# Derive the run id from the argument or the report's own run_id field.
run_id=${2:-$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('run_id',''))" "$report_file")}
if [ -z "$run_id" ]; then
    echo "error: could not determine run-id (pass it as the 2nd argument)" >&2
    exit 3
fi

account_url=${AZURE_STORAGE_ACCOUNT_URL:-$(get_azd_value AZURE_STORAGE_ACCOUNT_URL)}
container=${AZURE_REPORT_CONTAINER:-redteam}
# https://azst<token>.blob.core.windows.net -> azst<token>
account_name=$(printf '%s' "$account_url" | sed -E 's#^https?://##; s#\..*$##')

blob_name="reports/${run_id}.json"

echo "publishing $report_file"
echo "  account:   $account_name"
echo "  container: $container"
echo "  blob:      $blob_name"

az storage blob upload \
    --account-name "$account_name" \
    --auth-mode login \
    --container-name "$container" \
    --name "$blob_name" \
    --file "$report_file" \
    --content-type "application/json" \
    --overwrite \
    --only-show-errors \
    --output none

echo "published: https://${account_name}.blob.core.windows.net/${container}/${blob_name}"
