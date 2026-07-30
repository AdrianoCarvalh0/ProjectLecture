#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOMAIN="${1:-${PROJECTLECTURE_DOMAIN:-}}"
OUTPUT_FILE="$PROJECT_ROOT/.env.prod"

if [[ -z "$DOMAIN" ]]; then
    echo "Uso: scripts/azure/prepare-env.sh dominio.brazilsouth.cloudapp.azure.com" >&2
    exit 1
fi

if [[ -e "$OUTPUT_FILE" ]]; then
    echo "$OUTPUT_FILE já existe; ele não foi sobrescrito." >&2
    exit 1
fi

azure_key=""
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    azure_key="$(
        sed -n 's/^AZURE_SPEECH_KEY=//p' "$PROJECT_ROOT/.env" | head -n 1
    )"
fi
if [[ -z "$azure_key" ]]; then
    read -r -s -p "Cole a chave do Azure Speech: " azure_key
    echo
fi

google_client_id="${GOOGLE_OAUTH_CLIENT_ID:-}"
google_client_secret="${GOOGLE_OAUTH_CLIENT_SECRET:-}"
google_drive_api_key="${GOOGLE_DRIVE_API_KEY:-}"
google_project_number="${GOOGLE_CLOUD_PROJECT_NUMBER:-}"
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    [[ -n "$google_client_id" ]] || google_client_id="$(
        sed -n 's/^GOOGLE_OAUTH_CLIENT_ID=//p' "$PROJECT_ROOT/.env" | head -n 1
    )"
    [[ -n "$google_client_secret" ]] || google_client_secret="$(
        sed -n 's/^GOOGLE_OAUTH_CLIENT_SECRET=//p' "$PROJECT_ROOT/.env" | head -n 1
    )"
    [[ -n "$google_drive_api_key" ]] || google_drive_api_key="$(
        sed -n 's/^GOOGLE_DRIVE_API_KEY=//p' "$PROJECT_ROOT/.env" | head -n 1
    )"
    [[ -n "$google_project_number" ]] || google_project_number="$(
        sed -n 's/^GOOGLE_CLOUD_PROJECT_NUMBER=//p' "$PROJECT_ROOT/.env" | head -n 1
    )"
fi
if [[ -z "$azure_key" ]]; then
    echo "A chave do Azure Speech é obrigatória." >&2
    exit 1
fi

read -r -p "E-mail para os certificados HTTPS: " acme_email
if [[ -z "$acme_email" ]]; then
    echo "Informe um e-mail válido para o ACME." >&2
    exit 1
fi

django_secret="$(openssl rand -hex 48)"
mysql_password="$(openssl rand -hex 24)"
mysql_root_password="$(openssl rand -hex 24)"

umask 077
{
    echo "PROJECTLECTURE_DOMAIN=$DOMAIN"
    echo "ACME_EMAIL=$acme_email"
    echo "APP_VERSION=latest"
    echo
    echo "DJANGO_SECRET_KEY=$django_secret"
    echo "DJANGO_DEBUG=0"
    echo "DJANGO_ALLOWED_HOSTS=$DOMAIN"
    echo "DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN"
    echo "DJANGO_USE_X_FORWARDED_HOST=1"
    echo "DJANGO_SECURE_PROXY_SSL_HEADER=1"
    echo "DJANGO_SECURE_SSL_REDIRECT=1"
    echo "DJANGO_SESSION_COOKIE_SECURE=1"
    echo "DJANGO_CSRF_COOKIE_SECURE=1"
    echo "DJANGO_SECURE_HSTS_SECONDS=31536000"
    echo "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1"
    echo "DJANGO_SECURE_HSTS_PRELOAD=0"
    echo "DJANGO_SECURE_CROSS_ORIGIN_OPENER_POLICY=same-origin-allow-popups"
    echo "ALLOW_PUBLIC_REGISTRATION=1"
    echo
    echo "GOOGLE_OAUTH_CLIENT_ID=$google_client_id"
    echo "GOOGLE_OAUTH_CLIENT_SECRET=$google_client_secret"
    echo "GOOGLE_DRIVE_API_KEY=$google_drive_api_key"
    echo "GOOGLE_CLOUD_PROJECT_NUMBER=$google_project_number"
    echo
    echo "DB_ENGINE=mysql"
    echo "MYSQL_HOST=db"
    echo "MYSQL_PORT=3306"
    echo "MYSQL_DATABASE=projectlecture"
    echo "MYSQL_USER=projectlecture"
    echo "MYSQL_PASSWORD=$mysql_password"
    echo "MYSQL_ROOT_PASSWORD=$mysql_root_password"
    echo
    echo "CELERY_BROKER_URL=redis://redis:6379/0"
    echo "CELERY_RESULT_BACKEND=redis://redis:6379/1"
    echo "CELERY_TASK_ALWAYS_EAGER=0"
    echo "MAX_DOCUMENT_SIZE_MB=20"
    echo "MAX_CHARACTERS_PER_DOCUMENT=100000"
    echo "BOOK_PART_MAX_PAGES=10"
    echo "MAX_DOCUMENTS_PER_USER=10"
    echo "MAX_DOCUMENTS_PER_USER_PER_DAY=10"
    echo "MAX_READINGS_PER_USER_MONTH=10"
    echo "STREAM_CHUNK_CHARS=360"
    echo "STREAM_PREFETCH_CHUNKS=6"
    echo "TTS_ENVIRONMENT=production"
    echo
    echo "AZURE_SPEECH_KEY=$azure_key"
    echo "AZURE_SPEECH_REGION=brazilsouth"
    echo "AZURE_SPEECH_ENDPOINT=https://brazilsouth.api.cognitive.microsoft.com/"
    echo "AZURE_SPEECH_TIER=F0"
} > "$OUTPUT_FILE"

echo "$OUTPUT_FILE criado com permissão 600."
