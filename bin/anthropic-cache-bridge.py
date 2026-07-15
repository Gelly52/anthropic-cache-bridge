#!/usr/bin/env python3
"""Local bridge that composes prompt-cache and thinking-signature modules."""

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prompt_cache import PromptCacheModule
from thinking_signature import SignatureStore, ThinkingSignatureModule


CONFIG_PATH = os.path.expanduser(
    os.environ.get(
        "ANTHROPIC_CACHE_BRIDGE_CONFIG",
        "~/.config/anthropic-cache-bridge/config.json",
    )
)


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as config_file:
            config = json.load(config_file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read config {CONFIG_PATH}: {exc}")
    if not isinstance(config, dict):
        raise SystemExit(f"Config must contain a JSON object: {CONFIG_PATH}")
    return config


CONFIG = load_config()


def setting(env_name, config_name, default=""):
    return os.environ.get(env_name, CONFIG.get(config_name, default))


def boolean_setting(env_name, config_name, default=True):
    value = setting(env_name, config_name, default)
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


UPSTREAM = str(setting("UPSTREAM_URL", "upstream_url", "")).rstrip("/")
PORT = int(setting("PROXY_PORT", "proxy_port", 18787))
ENABLE_PROMPT_CACHE = boolean_setting(
    "ENABLE_PROMPT_CACHE", "enable_prompt_cache", True
)
ENABLE_THINKING_SIGNATURE = boolean_setting(
    "ENABLE_THINKING_SIGNATURE", "enable_thinking_signature", True
)
STABLE_USER_ID = str(setting("STABLE_USER_ID", "cache_affinity_user_id", ""))
CACHE_TTL = str(setting("CACHE_TTL", "cache_ttl", "5m"))
SIGNATURE_TTL = int(setting("SIGNATURE_TTL", "signature_ttl", 604800))
SIGNATURE_LIMIT = int(setting("SIGNATURE_LIMIT", "signature_limit", 2048))
SIGNATURE_DB_PATH = os.path.expanduser(
    str(
        setting(
            "SIGNATURE_DB_PATH",
            "signature_db_path",
            "~/.local/share/anthropic-cache-bridge/signatures.sqlite3",
        )
    )
)
DUMP_REQUESTS = boolean_setting("DUMP_REQUESTS", "dump_requests", False)
DUMP_DIR = os.path.expanduser(
    str(setting("DUMP_DIR", "dump_dir", "~/.local/share/anthropic-cache-bridge/dumps"))
)

try:
    PROMPT_CACHE = (
        PromptCacheModule(CACHE_TTL, STABLE_USER_ID)
        if ENABLE_PROMPT_CACHE
        else None
    )
except ValueError as exc:
    raise SystemExit(str(exc))

THINKING_SIGNATURE = None
if ENABLE_THINKING_SIGNATURE:
    THINKING_SIGNATURE = ThinkingSignatureModule(
        SignatureStore(SIGNATURE_LIMIT, SIGNATURE_TTL, SIGNATURE_DB_PATH)
    )


def is_anthropic_messages(path):
    return "/v1/messages" in path


def process_anthropic_request(raw_bytes):
    """Apply independent modules in the only safe mutation order."""
    request_model = ""
    try:
        body = json.loads(raw_bytes)
        if isinstance(body, dict):
            request_model = body.get("model", "")
    except json.JSONDecodeError:
        pass

    restored = 0
    if THINKING_SIGNATURE is not None:
        raw_bytes, restored = THINKING_SIGNATURE.restore_request(raw_bytes)

    cache_changed = False
    if PROMPT_CACHE is not None:
        raw_bytes, cache_changed = PROMPT_CACHE.process_request(raw_bytes)

    if DUMP_REQUESTS and b'"thinking"' in raw_bytes:
        os.makedirs(DUMP_DIR, mode=0o700, exist_ok=True)
        request_hash = hashlib.sha256(raw_bytes).hexdigest()[:12]
        dump_path = os.path.join(DUMP_DIR, f"request-{request_hash}.json")
        with open(dump_path, "wb") as dump_file:
            dump_file.write(raw_bytes)
        os.chmod(dump_path, 0o600)
        sys.stderr.write(f"[DIAG] dumped request to {dump_path}\n")

    return raw_bytes, request_model, cache_changed, restored


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if os.environ.get("DEBUG"):
            sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {fmt % args}\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        request_model = ""
        cache_changed = False
        restored = 0
        if is_anthropic_messages(self.path):
            raw, request_model, cache_changed, restored = process_anthropic_request(raw)
        sys.stderr.write(
            f"[DIAG] POST {self.path} len={length} "
            f"cache_changed={cache_changed} signatures_restored={restored}\n"
        )
        self._forward_raw(raw, request_model)

    def do_GET(self):
        upstream_url = UPSTREAM + self.path
        headers = {key: value for key, value in self.headers.items() if key.lower() != "host"}
        headers["Host"] = UPSTREAM.split("://", 1)[1].split("/", 1)[0]
        request = urllib.request.Request(upstream_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                self._write_response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as error:
            self._write_response(error.code, error.headers, error.read())

    def _forward_raw(self, raw_bytes, request_model=""):
        upstream_url = UPSTREAM + self.path
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "content-length")
        }
        headers["Host"] = UPSTREAM.split("://", 1)[1].split("/", 1)[0]
        headers["Content-Length"] = str(len(raw_bytes))
        request = urllib.request.Request(
            upstream_url, data=raw_bytes, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                data = response.read()
                captured = (
                    THINKING_SIGNATURE.capture_response(data, request_model)
                    if THINKING_SIGNATURE is not None
                    else 0
                )
                sys.stderr.write(
                    f"[DIAG] RESP {response.status} len={len(data)} "
                    f"signatures_captured={captured}\n"
                )
                self._write_response(response.status, response.headers, data)
        except urllib.error.HTTPError as error:
            data = error.read()
            sys.stderr.write(f"[DIAG] RESP ERR {error.code} len={len(data)}\n")
            self._write_response(error.code, error.headers, data)

    def _write_response(self, status, headers, data):
        self.send_response(status)
        for key, value in headers.items():
            if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    if not UPSTREAM:
        raise SystemExit(
            f"UPSTREAM_URL is required. Set it in {CONFIG_PATH} or the environment."
        )
    server = ThreadingHTTPServer(("127.0.0.1", PORT), ProxyHandler)
    print(
        f"anthropic-cache-bridge listening on 127.0.0.1:{PORT} -> {UPSTREAM} "
        f"prompt_cache={ENABLE_PROMPT_CACHE} "
        f"thinking_signature={ENABLE_THINKING_SIGNATURE}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.", flush=True)
        server.shutdown()
