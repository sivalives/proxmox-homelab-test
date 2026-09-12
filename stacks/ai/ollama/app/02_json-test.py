
import requests
import json

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:4b"

prompt = """
Extract the following information from the text.

Return ONLY valid JSON with these fields:
- name
- age
- city

Text:
"My name is Siva. I am 42 years old and I live in Bengaluru."
"""

response = requests.post(
    f"{OLLAMA_URL}/api/chat",
    json={
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {
            "num_thread": 8,
            "num_ctx": 2048,
            "temperature": 0,
            "num_predict": 200
        }
    },
    timeout=300
)

response.raise_for_status()

answer = response.json()["message"]["content"]

print("===== RAW RESPONSE =====")
print(answer)

print("\n===== PARSED JSON =====")
data = json.loads(answer)
print(data)

print("\n===== ACCESSING DATA =====")
print("Name:", data["name"])
print("Age:", data["age"])
print("City:", data["city"])
