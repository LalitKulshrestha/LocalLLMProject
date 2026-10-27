#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/../engines/ComfyUI"
test -x .venv/bin/python || { echo "Run ./scripts/install-engines.sh first"; exit 1; }
exec .venv/bin/python main.py --listen 127.0.0.1 --port 8188 --force-fp16
