
import ollama
import json

MODEL = "qwen3:4b"

response = ollama.chat(
    model=MODEL,
    messages=[
        {
            "role": "user",
            "content": """
            Give me information about Docker.

            Return the result as JSON with exactly these fields:
            topic
            category
            difficulty
            summary
            """
        }
    ],
    stream=False,
    think=False,
    format="json",
    options={
        "num_thread": 8,
        "num_ctx": 2048,
        "temperature": 0,
        "num_predict": 300
    }
)

text = response["message"]["content"]

print("RAW RESPONSE:")
print(text)

data = json.loads(text)

print("\nPYTHON DATA:")
print(data)

print("\nINDIVIDUAL FIELDS:")
print("Topic      :", data["topic"])
print("Category   :", data["category"])
print("Difficulty :", data["difficulty"])
print("Summary    :", data["summary"])