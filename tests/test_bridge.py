#!/usr/bin/env python3
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
BRIDGE = BIN / "anthropic-cache-bridge.py"
sys.path.insert(0, str(BIN))

from prompt_cache import PromptCacheModule
from thinking_signature import SignatureStore, ThinkingSignatureModule


def load_bridge(temp_dir, prompt_cache=True, thinking_signature=True):
    db_path = Path(temp_dir) / "signatures.sqlite3"
    config_path = Path(temp_dir) / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "upstream_url": "https://api.example.com",
                "enable_prompt_cache": prompt_cache,
                "enable_thinking_signature": thinking_signature,
                "cache_ttl": "5m",
                "signature_db_path": str(db_path),
                "dump_requests": False,
            }
        ),
        encoding="utf-8",
    )
    keys = (
        "ANTHROPIC_CACHE_BRIDGE_CONFIG",
        "ENABLE_PROMPT_CACHE",
        "ENABLE_THINKING_SIGNATURE",
        "SIGNATURE_DB_PATH",
    )
    previous = {key: os.environ.get(key) for key in keys}
    os.environ["ANTHROPIC_CACHE_BRIDGE_CONFIG"] = str(config_path)
    for key in keys[1:]:
        os.environ.pop(key, None)
    try:
        name = "bridge_test_" + next(tempfile._get_candidate_names())
        spec = importlib.util.spec_from_file_location(name, BRIDGE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, db_path
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def request(signature=""):
    return json.dumps(
        {
            "model": "claude-test",
            "system": [{"type": "text", "text": "system"}],
            "tools": [
                {
                    "name": "tool",
                    "description": "test",
                    "input_schema": {"type": "object"},
                }
            ],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "hello"}]},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "private reasoning",
                            "signature": signature,
                        }
                    ],
                },
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()


class PromptCacheTests(unittest.TestCase):
    def test_injects_three_breakpoints_without_changing_thinking(self):
        module = PromptCacheModule("5m")
        raw = request("valid-signature")
        changed, injected = module.process_request(raw)
        self.assertTrue(injected)
        self.assertEqual(changed.count(module.cache_control_json.encode()), 3)
        self.assertEqual(
            json.loads(raw)["messages"][1]["content"][0],
            json.loads(changed)["messages"][1]["content"][0],
        )

    def test_injection_is_idempotent(self):
        module = PromptCacheModule("5m")
        first, _ = module.process_request(request())
        second, changed = module.process_request(first)
        self.assertFalse(changed)
        self.assertEqual(second, first)


class ThinkingSignatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "signatures.sqlite3"
        self.store = SignatureStore(10, 60, str(self.path))
        self.module = ThinkingSignatureModule(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_across_store_instances(self):
        self.assertTrue(self.store.put("claude-test", "thinking", "signature"))
        second = SignatureStore(10, 60, str(self.path))
        self.assertEqual(second.get("claude-test", "thinking"), "signature")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_expiry(self):
        self.store.put("claude-test", "thinking", "signature")
        with sqlite3.connect(self.path) as database:
            database.execute("UPDATE signatures SET expires_at = ?", (time.time() - 1,))
        self.assertIsNone(self.store.get("claude-test", "thinking"))

    def test_restores_missing_signature(self):
        self.store.put("claude-test", "private reasoning", "restored-signature")
        restored, count = self.module.restore_request(request())
        self.assertEqual(count, 1)
        block = json.loads(restored)["messages"][1]["content"][0]
        self.assertEqual(block["signature"], "restored-signature")

    def test_captures_json_and_sse(self):
        response = json.dumps(
            {
                "model": "claude-test",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "json thinking",
                        "signature": "json signature",
                    }
                ],
            }
        ).encode()
        self.assertEqual(self.module.capture_response(response, ""), 1)
        self.assertEqual(
            self.store.get("claude-test", "json thinking"), "json signature"
        )

        events = [
            {"type": "message_start", "message": {"model": "claude-test"}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "sse thinking"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sse signature"},
            },
            {"type": "content_block_stop", "index": 0},
        ]
        sse = "\n".join("data: " + json.dumps(event) for event in events).encode()
        self.assertEqual(self.module.capture_response(sse, ""), 1)
        self.assertEqual(
            self.store.get("claude-test", "sse thinking"), "sse signature"
        )


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_both_modules_enabled(self):
        bridge, _ = load_bridge(self.temp.name, True, True)
        bridge.THINKING_SIGNATURE.store.put(
            "claude-test", "private reasoning", "restored-signature"
        )
        changed, model, cache_changed, restored = bridge.process_anthropic_request(
            request()
        )
        self.assertEqual(model, "claude-test")
        self.assertTrue(cache_changed)
        self.assertEqual(restored, 1)
        body = json.loads(changed)
        self.assertEqual(
            body["messages"][1]["content"][0]["signature"], "restored-signature"
        )
        self.assertEqual(changed.count(b'"cache_control"'), 3)

    def test_legacy_config_defaults_to_both_modules(self):
        bridge, _ = load_bridge(self.temp.name, True, True)
        legacy = Path(self.temp.name) / "legacy.json"
        legacy.write_text(
            json.dumps(
                {
                    "upstream_url": "https://api.example.com",
                    "signature_db_path": str(Path(self.temp.name) / "legacy.sqlite3"),
                }
            ),
            encoding="utf-8",
        )
        previous = os.environ.get("ANTHROPIC_CACHE_BRIDGE_CONFIG")
        os.environ["ANTHROPIC_CACHE_BRIDGE_CONFIG"] = str(legacy)
        try:
            name = "legacy_bridge_" + next(tempfile._get_candidate_names())
            spec = importlib.util.spec_from_file_location(name, BRIDGE)
            legacy_bridge = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(legacy_bridge)
            self.assertIsNotNone(legacy_bridge.PROMPT_CACHE)
            self.assertIsNotNone(legacy_bridge.THINKING_SIGNATURE)
        finally:
            if previous is None:
                os.environ.pop("ANTHROPIC_CACHE_BRIDGE_CONFIG", None)
            else:
                os.environ["ANTHROPIC_CACHE_BRIDGE_CONFIG"] = previous

    def test_prompt_cache_only_does_not_create_signature_db(self):
        bridge, db_path = load_bridge(self.temp.name, True, False)
        changed, _, cache_changed, restored = bridge.process_anthropic_request(request())
        self.assertTrue(cache_changed)
        self.assertEqual(restored, 0)
        self.assertFalse(db_path.exists())
        self.assertEqual(
            json.loads(changed)["messages"][1]["content"][0]["signature"], ""
        )

    def test_thinking_signature_only_does_not_add_cache_control(self):
        bridge, _ = load_bridge(self.temp.name, False, True)
        bridge.THINKING_SIGNATURE.store.put(
            "claude-test", "private reasoning", "restored-signature"
        )
        changed, _, cache_changed, restored = bridge.process_anthropic_request(request())
        self.assertFalse(cache_changed)
        self.assertEqual(restored, 1)
        self.assertNotIn(b'"cache_control"', changed)

    def test_both_modules_disabled_leave_request_unchanged(self):
        bridge, db_path = load_bridge(self.temp.name, False, False)
        raw = request()
        changed, _, cache_changed, restored = bridge.process_anthropic_request(raw)
        self.assertEqual(changed, raw)
        self.assertFalse(cache_changed)
        self.assertEqual(restored, 0)
        self.assertFalse(db_path.exists())

    def test_signature_restoration_runs_before_cache_injection(self):
        bridge, _ = load_bridge(self.temp.name, False, False)
        calls = []

        class SignatureSpy:
            def restore_request(self, raw):
                calls.append(("signature", raw))
                return raw + b"-signature", 1

        class CacheSpy:
            def process_request(self, raw):
                calls.append(("cache", raw))
                return raw + b"-cache", True

        bridge.THINKING_SIGNATURE = SignatureSpy()
        bridge.PROMPT_CACHE = CacheSpy()
        changed, _, _, _ = bridge.process_anthropic_request(b"raw")
        self.assertEqual(changed, b"raw-signature-cache")
        self.assertEqual(calls, [("signature", b"raw"), ("cache", b"raw-signature")])


if __name__ == "__main__":
    unittest.main()
