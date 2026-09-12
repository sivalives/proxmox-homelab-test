
import requests
import json

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen3:4b"

messages = [
    {
        "role": "system",
        "content": "You are a helpful assistant. Answer clearly and concisely."
    }
]


def chat(user_message):
    messages.append({
        "role": "user",
        "content": user_message
    })

    print("\n===== MESSAGES SENT TO LLM =====")
    print(json.dumps(messages, indent=2))
    print("================================\n")

    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "num_thread": 8,
                "num_ctx": 2048,
                "temperature": 0,
                "num_predict": 512
            }
        },
        timeout=300
    )

    response.raise_for_status()

    answer = response.json()["message"]["content"]

    messages.append({
        "role": "assistant",
        "content": answer
    })

    return answer


while True:
    user = input("You: ")

    if user.lower() == "exit":
        break

    print("AI:", chat(user))
