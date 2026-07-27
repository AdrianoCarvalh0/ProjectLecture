#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${PROJECTLECTURE_BACKUP_DIR:-$PROJECT_ROOT/backups}"
RETENTION_DAYS="${PROJECTLECTURE_BACKUP_RETENTION_DAYS:-7}"
COMPOSE=(docker compose --env-file .env.prod -f docker-compose.prod.yml)
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

cd "$PROJECT_ROOT"
mkdir -p "$BACKUP_DIR"
umask 077

"${COMPOSE[@]}" exec -T db sh -c \
    'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump --single-transaction --quick --lock-tables=false -uroot "$MYSQL_DATABASE"' \
    | gzip -9 > "$BACKUP_DIR/mysql-${timestamp}.sql.gz"

"${COMPOSE[@]}" exec -T web tar -czf - -C /app/media . \
    > "$BACKUP_DIR/media-${timestamp}.tar.gz"

find "$BACKUP_DIR" -type f -mtime "+$RETENTION_DAYS" \
    \( -name 'mysql-*.sql.gz' -o -name 'media-*.tar.gz' \) -delete

echo "Backup concluído em $BACKUP_DIR."
