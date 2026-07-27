#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AZURE_SUBSCRIPTION="${AZURE_SUBSCRIPTION:-Azure for Students}"
AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-projectlecture-prod}"
AZURE_ADMIN_USER="${AZURE_ADMIN_USER:-azureuser}"
AZURE_SSH_KEY_PATH="${AZURE_SSH_KEY_PATH:-$(cd && pwd)/.ssh/projectlecture_azure}"
AZURE_VM_SIZES="${AZURE_VM_SIZES:-Standard_B1s Standard_B2ats_v2}"
AZURE_LOCATIONS="${AZURE_LOCATIONS:-}"

if ! command -v az >/dev/null 2>&1; then
    echo "Azure CLI não encontrado." >&2
    exit 1
fi

if [[ ! -f "${AZURE_SSH_KEY_PATH}.pub" ]]; then
    echo "Chave pública não encontrada: ${AZURE_SSH_KEY_PATH}.pub" >&2
    echo "Execute primeiro scripts/azure/provision.sh para gerar a chave." >&2
    exit 1
fi

az account set --subscription "$AZURE_SUBSCRIPTION"
if [[ "$(az group exists --name "$AZURE_RESOURCE_GROUP")" != "true" ]]; then
    echo "Grupo de recursos não encontrado: $AZURE_RESOURCE_GROUP" >&2
    exit 1
fi

subscription_id="$(az account show --query id --output tsv)"
subscription_slug="${subscription_id//-/}"
azure_dns_label="${AZURE_DNS_LABEL:-projectlecture-${subscription_slug:0:10}}"

if [[ -z "$AZURE_LOCATIONS" ]]; then
    policy_assignments="$(mktemp)"
    if az policy assignment list \
        --scope "/subscriptions/$subscription_id" \
        --disable-scope-strict-match true \
        --output json >"$policy_assignments" 2>/dev/null; then
        AZURE_LOCATIONS="$(
            python - "$policy_assignments" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    assignments = json.load(source)

locations = []
for assignment in assignments:
    display_name = str(assignment.get("displayName", "")).lower()
    if "location" not in display_name and "region" not in display_name:
        continue
    for parameter_name, parameter in (assignment.get("parameters") or {}).items():
        normalized_name = parameter_name.lower()
        if "location" not in normalized_name and "region" not in normalized_name:
            continue
        value = parameter.get("value") if isinstance(parameter, dict) else None
        if isinstance(value, list):
            locations.extend(item for item in value if isinstance(item, str))

print(" ".join(dict.fromkeys(locations)))
PY
        )"
    fi
    rm -f "$policy_assignments"
fi

if [[ -n "$AZURE_LOCATIONS" ]]; then
    echo "Regiões permitidas pela política: $AZURE_LOCATIONS"
else
    AZURE_LOCATIONS="centralus southcentralus westus2 westus3 eastus northcentralus canadacentral westeurope northeurope swedencentral francecentral germanywestcentral switzerlandnorth uksouth"
    echo "A política não expôs sua lista; testando regiões candidatas."
fi

admin_cidr="${AZURE_ADMIN_CIDR:-}"
if [[ -z "$admin_cidr" ]]; then
    public_ip="$(curl --fail --silent --show-error https://api.ipify.org || true)"
    if [[ -z "$public_ip" ]]; then
        echo "Não foi possível descobrir o IP público do Cloud Shell." >&2
        exit 1
    fi
    admin_cidr="${public_ip}/32"
fi
ssh_public_key="$(<"${AZURE_SSH_KEY_PATH}.pub")"
validation_log="$(mktemp)"
trap 'rm -f "$validation_log"' EXIT

for location in $AZURE_LOCATIONS; do
    for vm_size in $AZURE_VM_SIZES; do
        echo "Validando $vm_size em $location..."
        if az deployment group validate \
            --resource-group "$AZURE_RESOURCE_GROUP" \
            --template-file "$PROJECT_ROOT/infra/azure/main.bicep" \
            --parameters \
                location="$location" \
                adminUsername="$AZURE_ADMIN_USER" \
                sshPublicKey="$ssh_public_key" \
                adminSourceCidr="$admin_cidr" \
                dnsLabelPrefix="$azure_dns_label" \
                vmSize="$vm_size" \
            --output none >"$validation_log" 2>&1; then
            echo
            echo "Combinação disponível:"
            echo "AZURE_LOCATION=$location AZURE_VM_SIZE=$vm_size ./scripts/azure/provision.sh"
            exit 0
        fi

        if ! grep -Eq "SkuNotAvailable|RequestDisallowedByAzure" "$validation_log"; then
            echo "A validação falhou por um motivo diferente de capacidade:" >&2
            grep -o '"code": "[^"]*"' "$validation_log" | sort -u >&2 || true
            exit 1
        fi
    done
done

echo "Nenhuma combinação gratuita testada está disponível neste momento." >&2
echo "Tente novamente mais tarde ou defina AZURE_LOCATIONS com outras regiões." >&2
exit 1
