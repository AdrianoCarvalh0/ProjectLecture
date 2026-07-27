#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Arquivo .env não encontrado. Copie .env.example e configure-o." >&2
    exit 1
fi

ngrok_token="$(
    sed -n 's/^NGROK_AUTHTOKEN=//p' "$ENV_FILE" | head -n 1
)"
ngrok_token="${ngrok_token%\"}"
ngrok_token="${ngrok_token#\"}"
ngrok_token="${ngrok_token%\'}"
ngrok_token="${ngrok_token#\'}"

if [[ -z "$ngrok_token" ]]; then
    echo "Defina NGROK_AUTHTOKEN no .env com o authtoken do painel do ngrok." >&2
    exit 1
fi

case "$ngrok_token" in
    github_pat_* | ghp_* | gho_* | ghu_* | ghs_* | ghr_*)
        echo "NGROK_AUTHTOKEN contém um token do GitHub. Revogue-o e use o authtoken do ngrok." >&2
        exit 1
        ;;
esac

cd "$PROJECT_ROOT"
docker compose \
    -f docker-compose.yml \
    -f docker-compose.ngrok.yml \
    up -d web ngrok

"$PROJECT_ROOT/scripts/ngrok-url.sh"
