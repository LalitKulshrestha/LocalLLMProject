#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/.."
test -x .venv/bin/uvicorn || { echo "Run ./scripts/bootstrap.sh first"; exit 1; }
set -a
test ! -f .env || source .env
set +a
host="${LOCALFORGE_HOST:-127.0.0.1}"
if [[ "$host" != "127.0.0.1" && "$host" != "localhost" && "${LOCALFORGE_ALLOW_REMOTE:-false}" != "true" ]]; then
  echo "Refusing a non-loopback bind. Set LOCALFORGE_ALLOW_REMOTE=true only after adding access controls."
  exit 1
fi
exec .venv/bin/uvicorn localforge.main:app --host "$host" --port "${LOCALFORGE_PORT:-8787}" --no-access-log
