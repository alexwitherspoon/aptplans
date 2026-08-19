#!/bin/bash
# Idempotent: import Bonsai into Ollama, then keep the weights loaded.
# Ollama has no published ports; this script talks to it with `compose exec`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_USER="${APP_USER:-aptplans}"
ENV_FILE="${ENV_FILE:-/home/${APP_USER}/.env.production}"
CONFIG="${REPO_ROOT}/config/ollama.json"

COMPOSE=(
    docker compose
    --env-file "${ENV_FILE}"
    -f "${REPO_ROOT}/docker/docker-compose.yml"
    -f "${REPO_ROOT}/docker/docker-compose.prod.yml"
)

if [ ! -f "${CONFIG}" ]; then
    echo "missing ${CONFIG}" >&2
    exit 1
fi
if [ ! -f "${ENV_FILE}" ]; then
    echo "missing ${ENV_FILE}" >&2
    exit 1
fi

set -a
# shellcheck source=/dev/null
. "${ENV_FILE}"
set +a

MODELS_PATH="${MODELS_PATH:-/var/lib/aptplans/models}"
model="$(jq -r '.model' "${CONFIG}")"
repo_id="$(jq -r '.repo_id' "${CONFIG}")"
filename="$(jq -r '.filename' "${CONFIG}")"
gguf_path="${MODELS_PATH}/${filename}"
url="https://huggingface.co/${repo_id}/resolve/main/${filename}"

cd "${REPO_ROOT}"

echo "Waiting for Ollama"
ready=0
for _ in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T ollama ollama list >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 2
done
if [ "${ready}" -ne 1 ]; then
    echo "Ollama did not become ready" >&2
    "${COMPOSE[@]}" logs --tail=80 ollama >&2 || true
    exit 1
fi

install -d -m 0755 "${MODELS_PATH}"
if [ ! -f "${gguf_path}" ]; then
    echo "downloading ${filename}"
    curl -fL --retry 5 --retry-delay 5 -C - \
        -A "aptplans.org" \
        -o "${gguf_path}.partial" \
        "${url}"
    mv "${gguf_path}.partial" "${gguf_path}"
fi

num_ctx="$(jq -r '.num_ctx' "${CONFIG}")"
temperature="$(jq -r '.temperature' "${CONFIG}")"
top_p="$(jq -r '.top_p' "${CONFIG}")"
top_k="$(jq -r '.top_k' "${CONFIG}")"
min_p="$(jq -r '.min_p' "${CONFIG}")"
repeat_penalty="$(jq -r '.repeat_penalty' "${CONFIG}")"
num_thread="$(jq -r '.num_thread' "${CONFIG}")"
num_gpu="$(jq -r '.num_gpu' "${CONFIG}")"
num_batch="$(jq -r '.num_batch' "${CONFIG}")"
system="$(jq -r '.system' "${CONFIG}")"

modelfile="${MODELS_PATH}/Modelfile.${model}"
cat > "${modelfile}" <<EOF
FROM /models/${filename}
PARAMETER num_ctx ${num_ctx}
PARAMETER temperature ${temperature}
PARAMETER top_p ${top_p}
PARAMETER top_k ${top_k}
PARAMETER min_p ${min_p}
PARAMETER repeat_penalty ${repeat_penalty}
PARAMETER num_thread ${num_thread}
PARAMETER num_gpu ${num_gpu}
PARAMETER num_batch ${num_batch}
SYSTEM """
${system}
"""
EOF

echo "creating ollama model ${model}"
"${COMPOSE[@]}" exec -T ollama ollama create "${model}" -f "/models/Modelfile.${model}"
echo "ollama model ${model} ready"
"${SCRIPT_DIR}/warmup-ollama.sh"
