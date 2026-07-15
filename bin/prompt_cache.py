"""Prompt-cache breakpoint injection for Anthropic message requests."""

import json

from raw_json import apply_edits, find_array_element_pos, find_key_value_pos, skip_ws


class PromptCacheModule:
    def __init__(self, cache_ttl="5m", affinity_user_id=""):
        if cache_ttl not in ("5m", "1h"):
            raise ValueError("cache_ttl must be 5m or 1h")
        self.cache_ttl = cache_ttl
        self.affinity_user_id = affinity_user_id
        self.cache_control_json = '"cache_control":' + json.dumps(
            {"type": "ephemeral", "ttl": cache_ttl}, separators=(",", ":")
        )

    def process_request(self, raw_bytes):
        try:
            raw_text = raw_bytes.decode("utf-8")
            body = json.loads(raw_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw_bytes, False
        if not isinstance(body, dict):
            return raw_bytes, False

        top_pos = skip_ws(raw_text, 0)
        if top_pos >= len(raw_text) or raw_text[top_pos] != "{":
            return raw_bytes, False

        edits = []

        def add_cache_control(object_pos):
            value_start, _ = find_key_value_pos(raw_text, object_pos, "cache_control")
            if value_start is not None:
                return
            inner = skip_ws(raw_text, object_pos + 1)
            suffix = "" if inner < len(raw_text) and raw_text[inner] == "}" else ","
            edits.append((inner, inner, self.cache_control_json + suffix))

        tools = body.get("tools")
        if isinstance(tools, list) and tools:
            tools_pos, _ = find_key_value_pos(raw_text, top_pos, "tools")
            if tools_pos is not None and raw_text[tools_pos] == "[":
                element_pos, _ = find_array_element_pos(
                    raw_text, tools_pos, len(tools) - 1
                )
                if element_pos is not None and raw_text[element_pos] == "{":
                    add_cache_control(element_pos)

        system = body.get("system")
        if isinstance(system, list) and system:
            system_pos, _ = find_key_value_pos(raw_text, top_pos, "system")
            if system_pos is not None and raw_text[system_pos] == "[":
                element_pos, _ = find_array_element_pos(
                    raw_text, system_pos, len(system) - 1
                )
                if element_pos is not None and raw_text[element_pos] == "{":
                    add_cache_control(element_pos)

        messages = body.get("messages")
        if isinstance(messages, list):
            latest_user = next(
                (
                    index
                    for index in range(len(messages) - 1, -1, -1)
                    if isinstance(messages[index], dict)
                    and messages[index].get("role") == "user"
                ),
                None,
            )
            if latest_user is not None:
                messages_pos, _ = find_key_value_pos(raw_text, top_pos, "messages")
                if messages_pos is not None and raw_text[messages_pos] == "[":
                    message_pos, _ = find_array_element_pos(
                        raw_text, messages_pos, latest_user
                    )
                    if message_pos is not None and raw_text[message_pos] == "{":
                        content_pos, _ = find_key_value_pos(
                            raw_text, message_pos, "content"
                        )
                        content = messages[latest_user].get("content")
                        if (
                            content_pos is not None
                            and raw_text[content_pos] == "["
                            and isinstance(content, list)
                            and content
                        ):
                            block_pos, _ = find_array_element_pos(
                                raw_text, content_pos, len(content) - 1
                            )
                            if block_pos is not None and raw_text[block_pos] == "{":
                                add_cache_control(block_pos)

        if self.affinity_user_id:
            metadata = body.get("metadata")
            if not isinstance(metadata, dict) or "user_id" not in metadata:
                metadata_pos, _ = find_key_value_pos(raw_text, top_pos, "metadata")
                user_json = '"user_id":' + json.dumps(self.affinity_user_id)
                if metadata_pos is None:
                    inner = skip_ws(raw_text, top_pos + 1)
                    suffix = "" if raw_text[inner] == "}" else ","
                    edits.append(
                        (inner, inner, '"metadata":{' + user_json + "}" + suffix)
                    )
                elif raw_text[metadata_pos] == "{":
                    inner = skip_ws(raw_text, metadata_pos + 1)
                    suffix = "" if raw_text[inner] == "}" else ","
                    edits.append((inner, inner, user_json + suffix))

        if not edits:
            return raw_bytes, False
        return apply_edits(raw_text, edits).encode("utf-8"), True
