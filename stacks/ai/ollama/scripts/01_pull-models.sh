#!/bin/bash
set -euo pipefail

CONTAINER="ollama"

MODELS=(
  "tinyllama:latest"
  "qwen3:4b"
)

echo "===== OLLAMA MODEL SETUP ====="

for MODEL in "${MODELS[@]}"; do
    echo
    echo ">>> Pulling $MODEL"
    docker exec "$CONTAINER" ollama pull "$MODEL"
done

echo
echo "===== INSTALLED MODELS ====="
docker exec "$CONTAINER" ollama list

echo
echo "Done."
