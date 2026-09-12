#!/bin/bash

###Create models force core to 8 cores / reduce context size- otherwise ollama takes all cores and is slow to respond to requests
### See models in /models directory. Each model has a Modelfile that specifies the model's configuration and parameters.
set -euo pipefail

OLLAMA_CONTAINER="ollama"
MODELS_DIR="/models"

MODELS=(
  "qwen3-mcp"
  "tinyllama"
)

for MODEL in "${MODELS[@]}"; do
  echo "===== CREATING MODEL: $MODEL ====="

  docker exec "$OLLAMA_CONTAINER" ollama create "$MODEL" \
    -f "$MODELS_DIR/$MODEL/Modelfile"

  echo
done

echo "===== AVAILABLE MODELS ====="
docker exec "$OLLAMA_CONTAINER" ollama list
