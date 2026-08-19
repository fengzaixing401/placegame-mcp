#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 0 ]; then exit 2; fi
if [ "$(id -u)" -ne 0 ]; then echo "must run as root" >&2; exit 1; fi
ROOT=/opt/placegame-mcp
SRC=$(cd "$(dirname "$0")" && pwd)
install -d -o root -g root -m 0755 "$ROOT" "$ROOT/deploy" "$ROOT/bin" "$ROOT/state"
install -o root -g root -m 0644 "$SRC/compose.yaml" "$ROOT/deploy/compose.yaml"
install -o root -g root -m 0644 "$SRC/placegame_deploy.py" "$ROOT/deploy/placegame_deploy.py"
install -o root -g root -m 0755 "$SRC/bin/deploy" "$ROOT/bin/deploy"
install -d -o root -g root -m 0700 "$ROOT/secrets"
umask 077
if [ ! -e "$ROOT/secrets/postgres_password" ]; then python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > "$ROOT/secrets/postgres_password"; fi
if [ ! -e "$ROOT/secrets/master_key" ]; then python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > "$ROOT/secrets/master_key"; fi
if [ ! -e "$ROOT/secrets/mcp_token" ]; then python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > "$ROOT/secrets/mcp_token"; fi
if [ ! -e "$ROOT/secrets/database_url" ]; then password=$(<"$ROOT/secrets/postgres_password"); printf 'postgresql+asyncpg://placegame:%s@postgres:5432/placegame' "$password" > "$ROOT/secrets/database_url"; fi
chmod 0600 "$ROOT/secrets"/*
chown root:root "$ROOT/secrets"/*
