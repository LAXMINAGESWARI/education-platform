#!/usr/bin/env python3
"""Tiny OpenAI-compatible mock. Python stdlib only."""

from __future__ import annotations

import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MIN_SECONDS = float(os.environ.get("MOCK_LATENCY_MIN_SECONDS", "1"))
MAX_SECONDS = float(os.environ.get("MOCK_LATENCY_MAX_SECONDS", "2"))
PORT = int(os.environ.get("MOCK_PORT", "8080"))
TEXT = os.environ.get("MOCK_TEXT", "This is a canned demo answer.")


def _pause() -> None:
    time.sleep(random.uniform(MIN_SECONDS, MAX_SECONDS))


def _chat(body: dict[str, object]) -> dict[str, object]:
    content = TEXT
    response_format = body.get("response_format")
    if isinstance(response_format, dict) and response_format.get("type") == "json_object":
        content = json.dumps(
            {"injection": False, "reason": "demo", "answer": TEXT, "sql": "select 1"}
        )
    return {
        "id": "chatcmpl-demo",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
    }


def _decisions(body: dict[str, object]) -> dict[str, object]:
    raw = body.get("questions")
    questions = raw if isinstance(raw, dict) and raw else {"decision": {"type": "noul"}}
    answers: dict[str, object] = {}
    for key, question in questions.items():
        kind = "noul"
        criteria: dict[str, object] = {}
        if isinstance(question, dict):
            if isinstance(question.get("type"), str):
                kind = str(question["type"])
            if isinstance(question.get("criteria"), dict):
                criteria = question["criteria"]
        if kind == "choice":
            choice = next(iter(criteria), "yes")
            answers[str(key)] = {"type": "choice", "choice": str(choice), "confidence": 0.9}
        elif kind == "score":
            answers[str(key)] = {"type": "score", "score": 2.0, "confidence": 0.9}
        else:
            answers[str(key)] = {"type": "noul", "noul": 0.05}
    model = body.get("model")
    return {
        "id": "dec_demo",
        "model": model if isinstance(model, str) and model else "typesafe/jev-1.14",
        "provider": "mock",
        "answers": answers,
        "usage": {"input_tokens": 12, "output_tokens": 2, "cost": 0.0},
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/health", "/ready"}:
            self._json({"status": "ok"})
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed: object = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            parsed = {}
        body = parsed if isinstance(parsed, dict) else {}
        path = self.path.split("?", 1)[0]
        if path in {"/chat/completions", "/v1/chat/completions"}:
            _pause()
            if body.get("stream") is True:
                self._stream()
            else:
                self._json(_chat(body))
            return
        if path == "/api/alpha/decisions":
            _pause()
            self._json(_decisions(body))
            return
        self.send_error(404)

    def _json(self, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        first = {
            "id": "chatcmpl-demo",
            "object": "chat.completion.chunk",
            "choices": [
                {"index": 0, "delta": {"role": "assistant", "content": TEXT}, "finish_reason": None}
            ],
        }
        stop = {
            "id": "chatcmpl-demo",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self.wfile.write(f"data: {json.dumps(first)}\n\n".encode())
        self.wfile.write(f"data: {json.dumps(stop)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
