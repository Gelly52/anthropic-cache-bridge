# Anthropic Cache Bridge

A dependency-free local reverse proxy for Anthropic-compatible APIs. It adds
prompt-cache breakpoints and repairs missing thinking signatures while
preserving the original request bytes.

中文说明：[README_CN.md](README_CN.md)

## Problem

Some Anthropic-compatible clients or adapters do not place `cache_control` at
useful prompt boundaries. Cache writes may occur while cache reads remain low.

Some clients also return an empty `signature` when replaying an assistant
`thinking` block. Anthropic-compatible upstreams can reject the next turn with:

```text
Invalid `signature` in `thinking` block
```

Anthropic Cache Bridge addresses both problems locally without changing the
client or upstream implementation.

## How it works

```text
Anthropic client
      |
      v
http://127.0.0.1:18787
      |
      v
Anthropic Cache Bridge
      |
      v
Anthropic-compatible upstream
```

For `/v1/messages` requests, the bridge runs two independent modules:

**Prompt Cache module**

1. Adds `cache_control` to the final tool definition.
2. Adds `cache_control` to the final system block.
3. Adds `cache_control` to the latest user message's final content block.
4. Optionally adds a stable `metadata.user_id` for upstream cache affinity.

Detailed guide: [docs/prompt-cache.md](docs/prompt-cache.md). This module is a
general Anthropic compatibility layer and is not tied to a specific client.

**Thinking Signature module**

1. Captures valid thinking signatures from JSON and SSE responses.
2. Restores empty or missing signatures by `(model, SHA-256(thinking))`.
3. Stores signatures in a local SQLite database for seven days by default.

Detailed guide: [docs/thinking-signature.md](docs/thinking-signature.md). This
module targets Cursor++ and similar clients that replay thinking blocks with
missing or unusable signatures.

The modules are independent: signature restoration does not require prompt
cache injection. Because both can edit the same request, the bridge always
processes them in this order:

```text
original request
    -> Thinking Signature: restore missing signatures
    -> Prompt Cache: inject cache_control with byte-level edits
    -> upstream
```

Response processing runs in the opposite direction: the Thinking Signature
module captures valid signatures only after the upstream response is complete.

Byte-level edits are used for requests. The bridge does not perform a
`json.loads` -> `json.dumps` round trip, because re-serialization can invalidate
thinking signatures.

## Requirements

- Python 3.9 or newer
- macOS with launchd, or Linux with systemd user services
- An Anthropic-compatible upstream exposing `/v1/messages`

No third-party Python packages are required.

## Quick start

```bash
git clone <your-repository-url> anthropic-cache-bridge
cd anthropic-cache-bridge
sh ./install.sh --upstream https://api.example.com
```

If a configuration already exists, the installer preserves it. Use
`--force-config` only when intentionally replacing the upstream and cache
settings:

```bash
sh ./install.sh --upstream https://api.example.com --force-config
```

On a machine that is already installed, `sh ./install.sh` may be run without
`--upstream` to reinstall the program while preserving the existing config.

If the upstream benefits from stable routing, supply a non-sensitive stable ID:

```bash
sh ./install.sh \
  --upstream https://api.example.com \
  --affinity-id my-local-cache-route
```

Then point the client's Anthropic base URL to:

```text
http://127.0.0.1:18787
```

Clients that expect the version suffix should use:

```text
http://127.0.0.1:18787/v1
```

Keep the API key in the client. The installer never asks for or stores it;
request headers are forwarded to the upstream.

Run the health check:

```bash
~/.local/bin/acbctl doctor
```

See [docs/clients.md](docs/clients.md) for OpenCode and Cursor++ examples.

## Management

```bash
acbctl status
acbctl start
acbctl stop
acbctl restart
acbctl logs
acbctl logs -f
acbctl db
acbctl doctor
acbctl backup
acbctl paths
```

Update an installed bridge from a checked-out project directory:

```bash
acbctl update .
```

The update command validates all four Python modules, backs up the current
installation, replaces them together, and restarts the user service.

## Configuration

Configuration is stored at:

```text
~/.config/anthropic-cache-bridge/config.json
```

Example:

```json
{
  "upstream_url": "https://api.example.com",
  "proxy_port": 18787,
  "enable_prompt_cache": true,
  "enable_thinking_signature": true,
  "cache_ttl": "5m",
  "cache_affinity_user_id": "",
  "signature_ttl": 604800,
  "signature_limit": 2048,
  "signature_db_path": "~/.local/share/anthropic-cache-bridge/signatures.sqlite3",
  "dump_requests": false
}
```

Supported `cache_ttl` values are `5m` and `1h`. Whether `1h` works depends on
the upstream's Anthropic compatibility and billing support.

The following environment variables override JSON values:

| Variable | Purpose |
|---|---|
| `UPSTREAM_URL` | Upstream base URL |
| `PROXY_PORT` | Local listening port |
| `STABLE_USER_ID` | `metadata.user_id` value |
| `ENABLE_PROMPT_CACHE` | Enable the Prompt Cache module |
| `ENABLE_THINKING_SIGNATURE` | Enable the Thinking Signature module |
| `CACHE_TTL` | `5m` or `1h` |
| `SIGNATURE_TTL` | Signature retention in seconds |
| `SIGNATURE_LIMIT` | Maximum signature records |
| `SIGNATURE_DB_PATH` | SQLite path |
| `DUMP_REQUESTS` | Enable sensitive request dumps when set to `1` |
| `DUMP_DIR` | Request dump directory |

## Verification

Prompt Cache module verification:

1. Send a sufficiently long first request.
2. Send a second request in the same session within the cache TTL.
3. Inspect the client's usage data for non-zero cache reads.

Thinking Signature module verification:

1. Start a new thinking-enabled session and send the first turn.
2. Confirm `signatures_captured=1` in `acbctl logs`.
3. Run `acbctl restart`.
4. Send another turn in the same session.
5. Confirm `signatures_restored=1` followed by `RESP 200`.

## Security and limitations

- The bridge listens only on `127.0.0.1`; do not expose it directly to a public
  network.
- API keys are not stored, but they pass through the local proxy in headers.
- The SQLite database stores signatures, model names, and hashes of thinking
  text. It does not store the original thinking text. Its permissions are `600`.
- Request body dumps are disabled by default. Enabling `dump_requests` can write
  prompts, tool arguments, source code, and other sensitive content to disk.
- Responses are currently buffered before being returned, including SSE
  responses. This enables reliable signature capture but may reduce streaming
  responsiveness for very long responses.
- A signature cannot be generated locally. Restoration works only after the
  bridge has observed a valid upstream signature for the exact model and
  thinking text.
- Prompt-cache TTL and signature retention are independent. Cache expiry lowers
  cache hits; missing signatures can produce a 400 response.
- Upstream implementations vary. Test cache statistics and thinking behavior
  before relying on this bridge in production workflows.

More detail: [docs/architecture.md](docs/architecture.md),
[docs/troubleshooting.md](docs/troubleshooting.md), and
[SECURITY.md](SECURITY.md).

## Tests

```bash
sh ./tests/run.sh
```

Tests use only temporary files and do not call an external API.

## Uninstall

```bash
sh ./uninstall.sh
```

Uninstall removes the service and binaries but preserves configuration, logs,
backups, and the signature database. Delete those data directories manually
only when they are no longer needed.

## License

MIT
