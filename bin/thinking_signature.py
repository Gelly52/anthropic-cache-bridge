"""Capture, persist, and restore Anthropic thinking signatures."""

import hashlib
import json
import os
import sqlite3
import threading
import time

from raw_json import apply_edits, find_array_element_pos, find_key_value_pos, skip_ws


class SignatureStore:
    def __init__(self, max_entries, ttl_seconds, db_path):
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.db_path = os.path.expanduser(db_path)
        self._lock = threading.Lock()
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS signatures (
                model TEXT NOT NULL,
                thinking_hash TEXT NOT NULL,
                signature TEXT NOT NULL,
                expires_at REAL NOT NULL,
                touched_at REAL NOT NULL,
                PRIMARY KEY (model, thinking_hash)
            )
            """
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS signatures_expires_idx ON signatures(expires_at)"
        )
        self._db.commit()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass
        with self._lock:
            self._prune(time.time())

    @staticmethod
    def _key(model, thinking):
        return model, hashlib.sha256(thinking.encode("utf-8")).hexdigest()

    def _prune(self, now):
        self._db.execute("DELETE FROM signatures WHERE expires_at <= ?", (now,))
        excess = (
            self._db.execute("SELECT COUNT(*) FROM signatures").fetchone()[0]
            - self.max_entries
        )
        if excess > 0:
            self._db.execute(
                """
                DELETE FROM signatures WHERE rowid IN (
                    SELECT rowid FROM signatures ORDER BY touched_at ASC LIMIT ?
                )
                """,
                (excess,),
            )
        self._db.commit()

    def put(self, model, thinking, signature):
        if not model or not thinking or not signature:
            return False
        key = self._key(model, thinking)
        now = time.time()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO signatures
                    (model, thinking_hash, signature, expires_at, touched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(model, thinking_hash) DO UPDATE SET
                    signature = excluded.signature,
                    expires_at = excluded.expires_at,
                    touched_at = excluded.touched_at
                """,
                (key[0], key[1], signature, now + self.ttl_seconds, now),
            )
            self._prune(now)
        return True

    def get(self, model, thinking):
        if not model or not thinking:
            return None
        key = self._key(model, thinking)
        now = time.time()
        with self._lock:
            self._prune(now)
            row = self._db.execute(
                """
                SELECT signature FROM signatures
                WHERE model = ? AND thinking_hash = ? AND expires_at > ?
                """,
                (key[0], key[1], now),
            ).fetchone()
            if row is None:
                return None
            self._db.execute(
                """UPDATE signatures SET touched_at = ?
                   WHERE model = ? AND thinking_hash = ?""",
                (now, key[0], key[1]),
            )
            self._db.commit()
            return row[0]


class ThinkingSignatureModule:
    def __init__(self, store):
        self.store = store

    def restore_request(self, raw_bytes):
        try:
            raw_text = raw_bytes.decode("utf-8")
            body = json.loads(raw_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw_bytes, 0
        if not isinstance(body, dict):
            return raw_bytes, 0
        model = body.get("model")
        messages = body.get("messages")
        if not isinstance(model, str) or not isinstance(messages, list):
            return raw_bytes, 0

        top_pos = skip_ws(raw_text, 0)
        messages_pos, _ = find_key_value_pos(raw_text, top_pos, "messages")
        if messages_pos is None or raw_text[messages_pos] != "[":
            return raw_bytes, 0

        edits = []
        for message_index, message in enumerate(messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            message_pos, _ = find_array_element_pos(
                raw_text, messages_pos, message_index
            )
            if message_pos is None or raw_text[message_pos] != "{":
                continue
            content_pos, _ = find_key_value_pos(raw_text, message_pos, "content")
            if content_pos is None or raw_text[content_pos] != "[":
                continue
            for block_index, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "thinking":
                    continue
                thinking = block.get("thinking")
                if not isinstance(thinking, str) or not thinking or block.get("signature"):
                    continue
                signature = self.store.get(model, thinking)
                if not signature:
                    continue
                block_pos, _ = find_array_element_pos(
                    raw_text, content_pos, block_index
                )
                if block_pos is None or raw_text[block_pos] != "{":
                    continue
                signature_pos, signature_end = find_key_value_pos(
                    raw_text, block_pos, "signature"
                )
                signature_json = json.dumps(signature)
                if signature_pos is not None:
                    edits.append((signature_pos, signature_end, signature_json))
                else:
                    inner = skip_ws(raw_text, block_pos + 1)
                    suffix = "" if raw_text[inner] == "}" else ","
                    edits.append(
                        (inner, inner, '"signature":' + signature_json + suffix)
                    )
        if not edits:
            return raw_bytes, 0
        return apply_edits(raw_text, edits).encode("utf-8"), len(edits)

    def capture_response(self, raw_bytes, request_model):
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return 0
        captured = 0

        def save(model, block):
            nonlocal captured
            if not isinstance(block, dict) or block.get("type") != "thinking":
                return
            if self.store.put(model, block.get("thinking"), block.get("signature")):
                captured += 1

        try:
            response = json.loads(text)
        except json.JSONDecodeError:
            response = None
        if isinstance(response, dict):
            model = response.get("model") or request_model
            content = response.get("content")
            if isinstance(content, list):
                for block in content:
                    save(model, block)
            return captured

        model = request_model
        thinking_blocks = {}
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "message_start":
                message = event.get("message")
                if isinstance(message, dict) and message.get("model"):
                    model = message["model"]
            elif event_type == "content_block_start":
                block = event.get("content_block")
                if isinstance(block, dict) and block.get("type") == "thinking":
                    thinking_blocks[event.get("index", 0)] = {
                        "type": "thinking",
                        "thinking": block.get("thinking", ""),
                        "signature": block.get("signature", ""),
                    }
            elif event_type == "content_block_delta":
                block = thinking_blocks.get(event.get("index", 0))
                delta = event.get("delta")
                if block is None or not isinstance(delta, dict):
                    continue
                if delta.get("type") == "thinking_delta":
                    block["thinking"] += delta.get("thinking", "")
                elif delta.get("type") == "signature_delta":
                    block["signature"] += delta.get("signature", "")
            elif event_type == "content_block_stop":
                save(model, thinking_blocks.pop(event.get("index", 0), None))
        for block in thinking_blocks.values():
            save(model, block)
        return captured
