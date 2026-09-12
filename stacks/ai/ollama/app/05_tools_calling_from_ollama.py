
import ollama

MODEL = "qwen3:4b"


# -------------------------
# TOOL
# -------------------------

def get_server_status(server):
    print(f"\n[TOOL EXECUTED] Checking {server}")

    return {
        "server": server,
        "status": "healthy",
        "cpu": 32,
        "memory": 61
    }


# -------------------------
# TOOL DEFINITION
# -------------------------

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_server_status",
            "description": "Get the current status of a server.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "Name of the server"
                    }
                },
                "required": ["server"]
            }
        }
    }
]


# -------------------------
# ASK MODEL
# -------------------------

response = ollama.chat(
    model=MODEL,
    messages=[
        {
            "role": "user",
            "content": "Check the status of server splunk01"
        }
    ],
    tools=tools,
    stream=False,
    think=False,
    options={
        "num_thread": 8,
        "num_ctx": 2048,
        "temperature": 0
    }
)


print("\n--- MODEL RESPONSE ---")
print(response)


# -------------------------
# EXECUTE TOOL
# -------------------------

if response["message"].get("tool_calls"):

    for call in response["message"]["tool_calls"]:

        name = call["function"]["name"]
        arguments = call["function"]["arguments"]

        print("\n--- TOOL CALL ---")
        print("Name:", name)
        print("Arguments:", arguments)

        if name == "get_server_status":

            result = get_server_status(
                arguments["server"]
            )

            print("\n--- TOOL RESULT ---")
            print(result)
