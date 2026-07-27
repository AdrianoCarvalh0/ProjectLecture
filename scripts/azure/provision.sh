#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AZURE_SUBSCRIPTION="${AZURE_SUBSCRIPTION:-Azure for Students}"
AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-projectlecture-prod}"
AZURE_LOCATION="${AZURE_LOCATION:-brazilsouth}"
AZURE_VM_SIZE="${AZURE_VM_SIZE:-Standard_B1s}"
AZURE_ADMIN_USER="${AZURE_ADMIN_USER:-azureuser}"
AZURE_SSH_KEY_PATH="${AZURE_SSH_KEY_PATH:-$(cd && pwd)/.ssh/projectlecture_azure}"

if ! command -v az >/dev/null 2>&1; then
    echo "Azure CLI não encontrado. Instale-o ou execute este script no Azure Cloud Shell." >&2
    exit 1
fi

if ! az account show >/dev/null 2>&1; then
    echo "Entre primeiro com: az login" >&2
    exit 1
fi

az account set --subscription "$AZURE_SUBSCRIPTION"
subscription_id="$(az account show --query id --output tsv)"
subscription_slug="${subscription_id//-/}"
AZURE_DNS_LABEL="${AZURE_DNS_LABEL:-projectlecture-${subscription_slug:0:10}}"

if [[ -z "${AZURE_ADMIN_CIDR:-}" ]]; then
    public_ip="$(curl --fail --silent --show-error https://api.ipify.org || true)"
    if [[ -z "$public_ip" ]]; then
        echo "Defina AZURE_ADMIN_CIDR com seu IP público, por exemplo 203.0.113.10/32." >&2
        exit 1
    fi
    AZURE_ADMIN_CIDR="${public_ip}/32"
fi

if [[ ! -f "${AZURE_SSH_KEY_PATH}.pub" ]]; then
    mkdir -p "$(dirname "$AZURE_SSH_KEY_PATH")"
    ssh-keygen -t ed25519 -f "$AZURE_SSH_KEY_PATH" -N "" -C projectlecture-azure
fi

ssh_public_key="$(<"${AZURE_SSH_KEY_PATH}.pub")"

az group create \
    --name "$AZURE_RESOURCE_GROUP" \
    --location "$AZURE_LOCATION" \
    --output none

az deployment group create \
    --name projectlecture-infra \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --template-file "$PROJECT_ROOT/infra/azure/main.bicep" \
    --parameters \
        adminUsername="$AZURE_ADMIN_USER" \
        sshPublicKey="$ssh_public_key" \
        adminSourceCidr="$AZURE_ADMIN_CIDR" \
        dnsLabelPrefix="$AZURE_DNS_LABEL" \
        vmSize="$AZURE_VM_SIZE" \
    --output table

fqdn="$(
    az network public-ip show \
        --resource-group "$AZURE_RESOURCE_GROUP" \
        --name pip-projectlecture \
        --query dnsSettings.fqdn \
        --output tsv
)"

echo
echo "Infraestrutura criada."
echo "URL futura: https://${fqdn}"
echo "SSH: ssh -i ${AZURE_SSH_KEY_PATH} ${AZURE_ADMIN_USER}@${fqdn}"
echo "Próximo passo: scripts/azure/deploy-app.sh"
