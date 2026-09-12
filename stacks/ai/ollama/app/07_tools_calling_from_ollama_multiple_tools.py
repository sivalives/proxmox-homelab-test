
import ollama

MODEL = "qwen3:4b"


# --------------------------------
# 1. REAL PYTHON FUNCTION
# --------------------------------

def get_server_status(server):
    print(f"\n[PYTHON] Executing get_server_status({server})")

    return {
        "server": server,
        "status": "healthy",
        "cpu": 32,
        "memory": 61
    }

def shutdown_server(server):
    print(f"\n[PYTHON] Executing shutdown_server({server})")

    return {
        "server": server,
        "status": "shutting_down",
        "cpu": 32,
        "memory": 61
    }


# --------------------------------
# 2. DESCRIBE TOOL TO THE LLM
# --------------------------------

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
    },
    {
        "type": "function",
        "function": {
            "name": "shutdown_server",
            "description": "Shutdown a server.",
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


# --------------------------------
# 3. USER MESSAGE
# --------------------------------

messages = [
    {
        "role": "user",
        "content": "shutdown server splunk01"
    }
]


# --------------------------------
# 4. FIRST LLM CALL
# --------------------------------

print("\n===== FIRST LLM CALL =====")

response = ollama.chat(
    model=MODEL,
    messages=messages,
    tools=tools,
    stream=False,
    think=False,
    options={
        "num_thread": 8,
        "num_ctx": 2048,
        "temperature": 0
    }
)

print("LLM response:")
print(response["message"])


# --------------------------------
# 5. ADD LLM RESPONSE TO MESSAGES
# --------------------------------

messages.append(response["message"])


# --------------------------------
# 6. CHECK IF LLM WANTS A TOOL
# --------------------------------

if response["message"].get("tool_calls"):

    for call in response["message"]["tool_calls"]:

        name = call["function"]["name"]
        arguments = call["function"]["arguments"]

        print("\n===== TOOL CALL =====")
        print("Tool:", name)
        print("Arguments:", arguments)


        # --------------------------------
        # 7. PYTHON EXECUTES THE TOOL
        # --------------------------------

        if name == "get_server_status":

            result = get_server_status(
                arguments["server"]
            )

            print("\nTool result:")
            print(result)
            messages.append({
                "role": "tool",
                "content": str(result)
            })            

        if name == "shutdown_server":
            result = shutdown_server(
                arguments["server"]
            )
            print("\nTool result:")
            print(result)
            # --------------------------------
            # 8. SEND TOOL RESULT BACK TO LLM
            # --------------------------------

            messages.append({
                "role": "tool",
                "content": str(result)
            })


# --------------------------------
# 9. SECOND LLM CALL
# --------------------------------

print("\n===== SECOND LLM CALL =====")

final_response = ollama.chat(
    model=MODEL,
    messages=messages,
    tools=tools,
    stream=False,
    think=False,
    options={
        "num_thread": 8,
        "num_ctx": 2048,
        "temperature": 0,
        "num_predict": 200
    }
)


print("\n===== FINAL ANSWER =====")
print(final_response["message"]["content"])
