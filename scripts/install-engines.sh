#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/.."
command -v git >/dev/null || { echo "Install git first: brew install git"; exit 1; }
command -v ffmpeg >/dev/null || { echo "Install FFmpeg first: brew install ffmpeg"; exit 1; }
python_bin="${LOCALFORGE_ENGINE_PYTHON:-/opt/homebrew/bin/python3.12}"
test -x "$python_bin" || { echo "Python 3.12 is required: brew install python@3.12"; exit 1; }
test -x .venv/bin/virtualenv || { echo "Run ./scripts/bootstrap.sh first"; exit 1; }
wheel_dir="${TMPDIR:-/tmp}/localforge-wheels"
mkdir -p "$wheel_dir"
if [[ ! -f "$wheel_dir/pip-25.3-py3-none-any.whl" ]]; then
  .venv/bin/pip download --no-deps pip==25.3 -d "$wheel_dir"
fi
if [[ ! -d engines/facefusion/.git ]]; then
  git clone --depth 1 https://github.com/facefusion/facefusion engines/facefusion
fi
.venv/bin/virtualenv --python "$python_bin" --pip 25.3 --extra-search-dir="$wheel_dir" engines/facefusion/.venv
(cd engines/facefusion && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python install.py default --skip-conda)
if [[ ! -d engines/ComfyUI/.git ]]; then
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI engines/ComfyUI
fi
.venv/bin/virtualenv --python "$python_bin" --pip 25.3 --extra-search-dir="$wheel_dir" engines/ComfyUI/.venv
engines/ComfyUI/.venv/bin/pip install -r engines/ComfyUI/requirements.txt
echo "Engines installed. Add ComfyUI models, then run ./scripts/start-comfyui.sh"
