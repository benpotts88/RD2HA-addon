#!/usr/bin/env bash
set -euo pipefail

export HASSIO_OPTIONS_PATH="${HASSIO_OPTIONS_PATH:-/data/options.json}"
export PLAYWRIGHT_STORAGE_STATE="${PLAYWRIGHT_STORAGE_STATE:-/config/storage_state.json}"

mkdir -p "$(dirname "${PLAYWRIGHT_STORAGE_STATE}")"

exec python -m src.main
