#!/usr/bin/env bash
# TSLIT-DSPy command deck launcher.
# Usage:  ./serve.sh [port]
set -euo pipefail
cd "$(dirname "$0")"

PORT="${1:-8780}"

if [ ! -x ".venv/bin/python" ]; then
  echo "No .venv found. Creating one and installing deps…" >&2
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip -q
  .venv/bin/python -m pip install -q "dspy>=2.5" python-dotenv
fi

echo "Starting TSLIT-DSPy command deck → http://127.0.0.1:${PORT}  (Ctrl+C to stop)"
exec .venv/bin/python -m tslit_dspy.web --port "${PORT}" --no-browser