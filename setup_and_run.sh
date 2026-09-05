#!/usr/bin/env bash
# Set up TSLIT-DSPy-AR from a source checkout and start the command deck.
# Drives the TSLITAnalyzer over the local OMLX server (Mac MLX runtime),
# the same backend the sibling projects (Contingency Atlas, Book Buddy) use.
#
# Usage:
#   ./setup_and_run.sh                       # OMLX backend (default)
#   ./setup_and_run.sh --port 9000           # run the deck on another port
#   ./setup_and_run.sh --skip-tests          # don't run the test suite
set -euo pipefail

cd "$(dirname "$0")"

PORT="${TSLIT_PORT:-8780}"
SKIP_TESTS=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --skip-tests) SKIP_TESTS=1 ; shift ;;
        --port=*)     PORT="${1#--port=}" ; shift ;;
        --port)       PORT="${2:-8780}" ; shift 2 ;;
        *) echo "ERROR: unknown option '$1'" >&2
           echo "Usage: $0 [--port N] [--skip-tests]" >&2
           exit 1 ;;
    esac
done

# OMLX is the only supported backend for the deck (Mac MLX server). The deck
# also speaks vLLM/DGX at runtime, but bring-up here is the local Mac path.
OMLX_URL="http://127.0.0.1:8000/v1"
OMLX_KEY="${OMLX_API_KEY:-test}"

# Dashboards from earlier runs survive the terminal that started them. A stale
# one keeps serving state from its own start time and holds the port, which
# makes a fresh start look like a hang. Clear them first. Only processes whose
# executable is python count: a shell, editor or grep whose command line merely
# mentions the module must never be a kill target.
dashboard_pids() {
    local pid comm
    for pid in $(pgrep -f ' -m tslit_dspy\\.web' 2>/dev/null || true); do
        [ "$pid" = "$$" ] && continue
        comm="$(ps -o comm= -p "$pid" 2>/dev/null || true)"
        case "${comm##*/}" in
            python | python[0-9]*) printf '%s\n' "$pid" ;;
        esac
    done
}

stop_stale_dashboards() {
    local pids
    pids="$(dashboard_pids)"
    if [ -n "$pids" ]; then
        echo "==> Stopping stale command deck process(es): $(echo "$pids" | tr '\n' ' ')"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 2
        pids="$(dashboard_pids)"
        if [ -n "$pids" ]; then
            # shellcheck disable=SC2086
            kill -9 $pids 2>/dev/null || true
            sleep 1
        fi
    fi
    if command -v lsof >/dev/null 2>&1 &&
       lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "ERROR: port $PORT is held by a process that is not a TSLIT deck:" >&2
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >&2
        echo "Stop it, or set TSLIT_PORT to a free port." >&2
        exit 1
    fi
}

# Pick a Python that satisfies the project (>=3.10). The miniconda 3.14 base
# is a good default on this machine; fall back gracefully.
if command -v python3.12 >/dev/null 2>&1; then
    PY=python3.12
elif command -v python3.13 >/dev/null 2>&1; then
    PY=python3.13
elif command -v python3.11 >/dev/null 2>&1; then
    PY=python3.11
elif [ -x "/Users/spider/miniconda3/bin/python" ]; then
    PY=/Users/spider/miniconda3/bin/python
else
    PY=python3
fi

echo "==> Using $PY ($("$PY" --version 2>&1))"

# Create the virtual environment if needed, then activate it
VENV=.venv
if [ ! -d "$VENV" ]; then
    echo "==> Creating virtual environment in $VENV"
    "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "==> Installing package with dev dependencies"
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'

# Verify the OMLX backend is serving before handing over to the deck.
echo "==> Checking OMLX backend at $OMLX_URL"
if curl -sS --max-time 3 -H "Authorization: Bearer $OMLX_KEY" \
        "$OMLX_URL/models" >/dev/null 2>&1; then
    MODEL="$(curl -sS --max-time 5 -H "Authorization: Bearer $OMLX_KEY" \
        "$OMLX_URL/models" \
        | python -c 'import json,sys; d=json.load(sys.stdin)["data"]; print(d[0]["id"] if d else "unknown")')"
    echo "    OMLX reachable — model: $MODEL"
elif curl -sS --max-time 3 "$OMLX_URL/models" >/dev/null 2>&1; then
    echo "    OMLX reachable but needs an API key; set OMLX_API_KEY (default: test)"
else
    echo "    WARNING: OMLX not reachable at $OMLX_URL"
    echo "    Start it, or the deck will serve but inference jobs will fail."
fi

if [ "$SKIP_TESTS" -ne 1 ]; then
    echo "==> Running test suite"
    python -m pytest
fi

stop_stale_dashboards

echo "==> Starting TSLIT-DSPy command deck at http://127.0.0.1:$PORT (Ctrl+C to stop)"
python -m tslit_dspy.web --port "$PORT"