#!/bin/bash

MODELS=(
  "tinyllama:latest"
  "qwen3:4b"
)

echo "===== MODEL ====="
echo "$MODEL"

echo
echo "===== TEST ====="

for MODEL in "${MODELS[@]}"; do
  echo "===== TESTING MODEL: $MODEL ====="
  time curl -s http://localhost:11434/api/generate \
  -d "{
    \"model\":\"$MODEL\",
    \"prompt\":\"Reply with exactly: OK\",
    \"stream\":false,
    \"think\":false,
    \"options\":{
      \"num_predict\":10
    }
  }" | jq .
  
  echo
done


echo
echo "===== RUNNING MODELS ====="
docker exec ollama ollama ps