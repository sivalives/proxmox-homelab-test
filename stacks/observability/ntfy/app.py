import os
import json
import urllib.request
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

NTFY_URL = os.getenv("NTFY_URL", "http://ntfy:80")
NTFY_USER = os.getenv("NTFY_USER", "")
NTFY_PASSWORD = os.getenv("NTFY_PASSWORD", "")
PORT = int(os.getenv("PORT", "5000"))


def get_messages(topic):
    url = f"{NTFY_URL.rstrip('/')}/{topic}/json?poll=1"

    request = urllib.request.Request(url)

    if NTFY_USER:
        credentials = f"{NTFY_USER}:{NTFY_PASSWORD}"
        encoded = base64.b64encode(credentials.encode()).decode()
        request.add_header("Authorization", f"Basic {encoded}")

    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode()

    messages = []

    for line in body.splitlines():
        line = line.strip()

        if not line:
            continue

        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        if item.get("event") != "message":
            continue

        messages.append({
            "id": item.get("id"),
            "time": item.get("time"),
            "title": item.get("title", "ntfy"),
            "message": item.get("message", ""),
            "priority": item.get("priority", 3),
            "tags": item.get("tags", [])
        })

    # Newest first
    messages.reverse()

    return messages


class Handler(BaseHTTPRequestHandler):

    def send_json(self, data):
        body = json.dumps(data).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):

        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self.send_json({"status": "ok"})
            return

        if parsed.path == "/notifications":

            query = parse_qs(parsed.query)

            topic = query.get(
                "topic",
                ["proxmox_alerts"]
            )[0]

            try:
                messages = get_messages(topic)

                self.send_json({
                    "data": messages
                })

            except Exception as e:

                self.send_json({
                    "data": [],
                    "error": str(e)
                })

            return

        self.send_json({
            "error": "not found"
        })


server = HTTPServer(("0.0.0.0", PORT), Handler)

print(f"ntfy-history listening on port {PORT}")

server.serve_forever()