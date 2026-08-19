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
write_atomic() {
  target=$1
  shift
  if [ -e "$target" ]; then return 0; fi
  temp=$(mktemp "$ROOT/secrets/.tmp.XXXXXX")
  chmod 0600 "$temp"
  chown root:root "$temp"
  if ! "$@" > "$temp"; then
    rm -f "$temp"
    return 1
  fi
  if ln "$temp" "$target"; then
    rm -f "$temp"
    return 0
  fi
  rm -f "$temp"
  [ -e "$target" ]
}
write_token() { python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")'; }
write_database_url() {
  password=$(<"$ROOT/secrets/postgres_password")
  printf 'postgresql+asyncpg://placegame:%s@postgres:5432/placegame' "$password"
}
write_atomic "$ROOT/secrets/postgres_password" write_token
write_atomic "$ROOT/secrets/master_key" write_token
write_atomic "$ROOT/secrets/mcp_token" write_token
write_atomic "$ROOT/secrets/database_url" write_database_url
chmod 0600 "$ROOT/secrets"/*
chown root:root "$ROOT/secrets"/*
