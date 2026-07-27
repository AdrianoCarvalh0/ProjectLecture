#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-projectlecture-prod}"
AZURE_ADMIN_USER="${AZURE_ADMIN_USER:-azureuser}"
AZURE_SSH_KEY_PATH="${AZURE_SSH_KEY_PATH:-$(cd && pwd)/.ssh/projectlecture_azure}"

for command_name in az ssh scp rsync; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Comando obrigatório ausente: $command_name" >&2
        exit 1
    fi
done

fqdn="$(
    az network public-ip show \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --name pip-projectlecture \
        --query dnsSettings.fqdn \
        --output tsv
)"
target="${AZURE_ADMIN_USER}@${fqdn}"
ssh_options=(-i "$AZURE_SSH_KEY_PATH" -o StrictHostKeyChecking=accept-new)

ssh "${ssh_options[@]}" "$target" "sudo cloud-init status --wait"

if [[ ! -f "$PROJECT_ROOT/.env.prod" ]]; then
    "$PROJECT_ROOT/scripts/azure/prepare-env.sh" "$fqdn"
fi

rsync \
    --archive \
    --compress \
    --exclude .git/ \
    --exclude .env \
    --exclude .env.prod \
    --exclude .venv/ \
    --exclude backups/ \
    --exclude media/ \
    --exclude staticfiles/ \
    --exclude __pycache__/ \
    -e "ssh -i $AZURE_SSH_KEY_PATH -o StrictHostKeyChecking=accept-new" \
    "$PROJECT_ROOT/" "$target:/opt/projectlecture/"

scp "${ssh_options[@]}" "$PROJECT_ROOT/.env.prod" \
    "$target:/opt/projectlecture/.env.prod"

ssh "${ssh_options[@]}" "$target" "
    set -e
    cd /opt/projectlecture
    chmod 600 .env.prod
    docker compose --env-file .env.prod -f docker-compose.prod.yml config --quiet
    sudo systemctl daemon-reload
    sudo systemctl enable projectlecture.service
    sudo systemctl restart projectlecture.service
    sudo systemctl enable --now projectlecture-backup.timer
    docker compose --env-file .env.prod -f docker-compose.prod.yml ps
"

echo
echo "Deploy concluído: https://${fqdn}"
