#Only difference its prints like chatGpt style, right from question is asked - Streaming model response, no need to wait for the whole response to be generated before printing it out.
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

    # Keep system message + latest messages
    messages = [messages[0]] + messages[1:][-MAX_MESSAGES:]

    print("\nAI: ", end="", flush=True)

    full_response = ""

    response = ollama.chat(
        model=MODEL,
        messages=messages,
        stream=True,
        think=False,
        options={
            "num_thread": 8,
            "num_ctx": 2048,
            "temperature": 0,
            "num_predict": 100
        }
    )

    for chunk in response:
        text = chunk["message"]["content"]

        print(text, end="", flush=True)

        full_response += text

    print()

    messages.append({
        "role": "assistant",
        "content": full_response
    })
