#!/usr/bin/env bash
set -euo pipefail

AZURE_SUBSCRIPTION="${AZURE_SUBSCRIPTION:-Azure for Students}"
AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-projectlecture-prod}"
AZURE_MONTHLY_BUDGET="${AZURE_MONTHLY_BUDGET:-8}"
AZURE_BUDGET_EMAIL="${AZURE_BUDGET_EMAIL:-}"

if [[ -z "$AZURE_BUDGET_EMAIL" ]]; then
    echo "Defina AZURE_BUDGET_EMAIL para receber os alertas." >&2
    exit 1
fi

az account set --subscription "$AZURE_SUBSCRIPTION"
start_date="$(date +%Y-%m-01)"
end_date="$(date -d '+1 year' +%Y-%m-01)"
time_period="{\"start-date\":\"${start_date}\",\"end-date\":\"${end_date}\"}"
notifications="$(
    printf '{"Actual80":{"enabled":true,"operator":"GreaterThanOrEqualTo","contact-emails":["%s"],"threshold":80.0},"Actual100":{"enabled":true,"operator":"GreaterThanOrEqualTo","contact-emails":["%s"],"threshold":100.0}}' \
        "$AZURE_BUDGET_EMAIL" "$AZURE_BUDGET_EMAIL"
)"

az consumption budget create-with-rg \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --budget-name projectlecture-monthly \
    --amount "$AZURE_MONTHLY_BUDGET" \
    --category Cost \
    --time-grain Monthly \
    --time-period "$time_period" \
    --notifications "$notifications" \
    --output table
