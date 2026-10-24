#!/usr/bin/env bash
# sovereign_memory — the entry point (SA_90).
#
# The only way in, from a terminal, from another agent, from the ecosystem
# console or from the user service. It resolves the anchor and the interpreter
# per Law 30, then hands off to the launcher. It holds no logic of its own
# beyond that, so starting by hand, by this file, or by the service behaves
# identically.
#
# The reset modes SA_90 requires, offered on every start and passed straight
# through (the runtime says what each clears; this service keeps no generated
# state, and no mode ever clears the memory database, which is operator content):
#   (no flag)      normal start — supersedes a previous copy of this service
#   --reset        generated state (none is kept here)
#   --deep-reset   generated state and logs (none are kept here)
#
# The interpreter is SOVEREIGN_PYTHON, which the environment must give: the
# user service sets it, and by hand it is this project's micromamba environment.
# The memory database is the first argument or SOVEREIGN_MEMORY_DATABASE. Its
# address for the health door is SOVEREIGN_MEMORY_HTTP_HOST and
# SOVEREIGN_MEMORY_HTTP_PORT, and the registry's Redis is REDIS_HOST and
# REDIS_PORT; this file sets none of them. The Sovereign tree is
# SOVEREIGN_ROOT when the environment gives it; otherwise the launcher reads
# it from machine.json at the protocol root, where the machine declares it.

set -euo pipefail

SERVICE_ID="sovereign_memory"

# The anchor: the environment's value, or the layout this script sits in. This
# script lives at the project root, so the agent root is its own directory —
# derived, never a literal home path.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export AGENT_ROOT="${AGENT_ROOT:-$HERE}"

# The interpreter, per Law 30: the environment's value, and a plain refusal
# when it is not given or not executable.
if [ -z "${SOVEREIGN_PYTHON:-}" ] || [ ! -x "$SOVEREIGN_PYTHON" ]; then
    echo "$SERVICE_ID did not start: SOVEREIGN_PYTHON must name this project's" \
         "interpreter (its micromamba environment's python); it is" \
         "'${SOVEREIGN_PYTHON:-not set}'." >&2
    exit 1
fi

export PYTHONDONTWRITEBYTECODE=1

# exec, not a call: the launcher becomes this process, so the process that holds
# the address is running the launcher's own path. That is what take_port matches
# on when a later start supersedes a previous copy of this service, and what
# keeps it from ever matching anything else (Law 38's supersession guard).
exec "$SOVEREIGN_PYTHON" "$AGENT_ROOT/sovereign_memory_main.py" "$@"
