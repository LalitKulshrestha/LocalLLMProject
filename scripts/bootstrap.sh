#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/.."
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
mkdir -p data/{uploads,outputs,logs} workflows engines
test -f .env || cp .env.example .env
echo "LocalForge ready. Run ./scripts/start.sh"
