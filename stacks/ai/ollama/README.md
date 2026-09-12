## Issue : create a  model to force to 4 threads, by default ollama was hitting 6 threads and it really slowed things. 

cat > /root/Modelfile <<'EOF'
FROM qwen3:4b
PARAMETER num_ctx 4096
PARAMETER num_thread 4
EOF

ollama create qwen3-mcp -f /root/Modelfile


time curl http://localhost:11434/api/generate \
  -d '{
    "model":"qwen3-mcp:latest",
    "prompt":"Explain in 50 words why MCP is useful for AI agents.",
    "stream":false,
    "think":false,
    "options":{
      "num_thread":4,
      "num_predict":50,
      "num_ctx":4096
    }
  }'

