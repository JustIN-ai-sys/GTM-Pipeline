"""Tiny HTTP receiver for FullEnrich webhooks (standard library only)."""

from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gtm_pipeline.db import connect
from gtm_pipeline.enrich import apply_outcomes, parse_outcomes

MAX_BODY_BYTES = 2_000_000
WEBHOOK_PATH = "/webhook/fullenrich"


class WebhookHandler(BaseHTTPRequestHandler):
    db_path: str = "gtm.db"
    token: str | None = None

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path != WEBHOOK_PATH:
            self._send(404, {"error": "not found"})
            return
        if self.token:
            supplied = parse_qs(parsed.query).get("token", [""])[0]
            if not hmac.compare_digest(supplied, self.token):
                self._send(401, {"error": "invalid token"})
                return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(400, {"error": "bad body size"})
            return
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
            return
        conn = connect(self.db_path)
        try:
            result = apply_outcomes(conn, parse_outcomes(body), source="fullenrich_webhook")
        finally:
            conn.close()
        self._send(200, {"updated": result["stored"], "not_found": result["not_found"]})

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Log only the path, never the query string (it carries the token).
        status = args[1] if len(args) > 1 else ""
        print(f"webhook {self.command} {urlparse(self.path).path} -> {status}")


def make_server(
    db_path: str, token: str | None, host: str = "127.0.0.1", port: int = 8080
) -> ThreadingHTTPServer:
    handler = type("BoundWebhookHandler", (WebhookHandler,), {"db_path": db_path, "token": token})
    return ThreadingHTTPServer((host, port), handler)
