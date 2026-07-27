#!/usr/bin/env bash
set -euo pipefail

for _ in $(seq 1 20); do
    response="$(curl --fail --silent http://127.0.0.1:4040/api/tunnels || true)"
    if [[ -n "$response" ]]; then
        url="$(
            python -c \
                'import json,sys; data=json.load(sys.stdin); print(next((t["public_url"] for t in data.get("tunnels", []) if t["public_url"].startswith("https://")), ""))' \
                <<<"$response"
        )"
        if [[ -n "$url" ]]; then
            echo "$url"
            exit 0
        fi
    fi
    sleep 1
done

echo "O túnel ainda não publicou uma URL. Veja: docker compose -f docker-compose.yml -f docker-compose.ngrok.yml logs ngrok" >&2
exit 1
