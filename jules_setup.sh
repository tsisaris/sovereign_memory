#!/usr/bin/env bash
# Sets up a cloud machine (Jules or GitHub) to run sovereign_memory and its
# checks. Installs uv, then this project's packages and its checkers, all from
# public package indexes. Stops at the first failure and says which step.
set -euo pipefail

step() { printf '\n== %s\n' "$*"; }

step "uv (the package installer)"
if ! command -v uv >/dev/null 2>&1; then
    python3 -m pip install --quiet "uv==0.12.23"
fi
uv --version

step "this project's packages (sovereign_memory, Python 3.12) and its gating checkers"
uv sync --python "3.12" --group dev

step "ready: run uv run pre-commit run --all-files, then uv run pytest"
