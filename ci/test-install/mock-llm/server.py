#!/usr/bin/env python3
"""
Minimal OpenAI-compatible mock LLM server for CI testing.
Responds to /v1/chat/completions and /v1/models with valid responses.
No external dependencies — standard library only.
"""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockLLMHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            self._json(200, {
                "object": "list",
                "data": [{"id": "mock-model", "object": "model", "created": int(time.time())}],
            })
        elif self.path in ("/ready", "/health"):
            self._text(200, "ready")
        else:
            self._text(404, "not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid JSON"})
            return

        if self.path == "/v1/chat/completions":
            model = req.get("model", "mock-model")
            self._json(200, {
                "id": "chatcmpl-mock-001",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            })
        else:
            self._json(404, {"error": "endpoint not found"})

    def _json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, text: str):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[mock-llm] {self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    port = 8080
    server = HTTPServer(("0.0.0.0", port), MockLLMHandler)
    print(f"[mock-llm] Listening on :{port}", flush=True)
    server.serve_forever()
