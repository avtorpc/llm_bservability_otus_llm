#!/bin/sh
set -eu

: "${OLLAMA_MODEL:?Set OLLAMA_MODEL to the model tag to install}"

ollama serve &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true; wait "$server_pid" || true' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

until ollama list >/dev/null 2>&1; do
    kill -0 "$server_pid" || exit 1
    sleep 1
done

# The named volume keeps the model across container rebuilds and restarts.
if ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
    ollama pull "$OLLAMA_MODEL"
fi

wait "$server_pid"
