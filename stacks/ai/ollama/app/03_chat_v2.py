
import ollama

MODEL = "qwen3:4b"
MAX_MESSAGES = 10

messages = [
    {
        "role": "system",
        "content": "You are a helpful assistant."
    }
]

while True:
    user_input = input("\nYou: ")

    if user_input.lower() in ["exit", "quit"]:
        break

    messages.append({
        "role": "user",
        "content": user_input
    })

    # Keep system message + latest message
    messages = [messages[0]] + messages[1:][-MAX_MESSAGES:]

    print("\n--- BEFORE REQUEST ---")
    print("Messages:", len(messages))

    response = ollama.chat(
        model=MODEL,
        messages=messages,
        stream=False,
        think=False,
        options={
            "num_thread": 8,
            "num_ctx": 2048,
            "temperature": 0,
            "num_predict": 1500 #this will generate a longer response
        }
    )

    answer = response["message"]["content"]

    messages.append({
        "role": "assistant",
        "content": answer
    })

    print("AI:", answer)

    print("\n--- TOKENS ---")
    print("Input tokens :", response.get("prompt_eval_count"))
    print("Output tokens:", response.get("eval_count"))
    print("Context limit: 2048")

    print("\n--- MEMORY ---")
    for message in messages:
        print(f"{message['role']}: {message['content'][:80]}")
