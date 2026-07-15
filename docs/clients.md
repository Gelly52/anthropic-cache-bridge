# Client setup

The bridge does not know how to edit client configuration safely. Point only
the Anthropic-compatible provider that should use the bridge to the local
endpoint. Keep the API key in the client.

## OpenCode

In `~/.config/opencode/opencode.json`, set the relevant provider's URL to:

```json
"baseURL": "http://127.0.0.1:18787/v1"
```

The provider name and API key are client-specific. Make a backup before editing.
Verify with:

```bash
acbctl doctor
```

Then run two turns in one OpenCode session and check the usage data for a
non-zero `cache.read` value.

## Cursor++ and similar clients

Set the Anthropic provider's base URL to:

```text
http://127.0.0.1:18787
```

Some clients expect `/v1`; follow that client's convention. Start a new
thinking-enabled session for the first verification. After the first response,
the bridge log should contain `signatures_captured=1`. After a restart, the next
turn should contain `signatures_restored=1` and `RESP 200`.

Prompt Cache and Thinking Signature can be disabled separately in
`~/.config/anthropic-cache-bridge/config.json`:

```json
{
  "enable_prompt_cache": true,
  "enable_thinking_signature": true
}
```

## Other clients

Any client that sends Anthropic `/v1/messages` requests can use the bridge. The
client must preserve assistant thinking text between turns if it expects
signature restoration. Non-Anthropic routes are forwarded without cache or
signature changes.
