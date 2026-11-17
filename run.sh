#!/usr/bin/env bash
# Local online runtime: real Claude if the SDK+key are present (auto-falls back to the
# offline mock otherwise). Set VANTAGE_LLM=mock to force offline.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install -q --upgrade pip pytest anthropic >/dev/null 2>&1 || true
echo "[vantage] running tests…"; ./.venv/bin/python -m pytest -q tests/ || true
export VANTAGE_PORT="${VANTAGE_PORT:-8060}"
export VANTAGE_LLM="${VANTAGE_LLM:-anthropic}"
export VANTAGE_MODEL="${VANTAGE_MODEL:-claude-haiku-4-5-20251001}"
export VANTAGE_MAX_FILES="${VANTAGE_MAX_FILES:-200}"
export VANTAGE_SCHEDULE_S="${VANTAGE_SCHEDULE_S:-1800}"
export PYTHONUNBUFFERED=1
echo "[vantage] serving http://127.0.0.1:${VANTAGE_PORT} (llm=${VANTAGE_LLM})"
exec ./.venv/bin/python -m vantage.server
