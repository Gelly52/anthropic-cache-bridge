# Troubleshooting

For focused guidance, see [prompt-cache.md](prompt-cache.md) and
[thinking-signature.md](thinking-signature.md).

## Run the health check first

```bash
acbctl doctor
acbctl status
acbctl logs 200
```

## Connection refused

The service is not listening or the client is using the wrong port. Check:

```bash
acbctl status
lsof -nP -iTCP:18787 -sTCP:LISTEN
```

Start or restart the service:

```bash
acbctl start
acbctl restart
```

## No cache reads

Check that:

- the client uses `http://127.0.0.1:18787` (or `/v1` as required);
- both turns use the same session;
- the prompt is long enough for the upstream minimum cache size;
- the second turn is within the configured cache TTL;
- the upstream supports the requested cache TTL.

## Invalid thinking signature

Look for:

```text
signatures_captured=1
signatures_restored=1
RESP 200
```

If `signatures_restored=0`, the model/thinking hash did not match a stored
signature. Start a new session and capture the first successful response.

If `signatures_restored` is positive but the upstream still returns 400, the
upstream may reject a historical signature, the client may have modified the
thinking text, or the request may contain multiple conflicting thinking blocks.
Preserve the SQLite database and logs before changing anything.

## SQLite database

```bash
acbctl db
```

The default database is:

```text
~/.local/share/anthropic-cache-bridge/signatures.sqlite3
```

The database is intentionally local and private. Deleting it removes all
cross-restart signature state; it does not affect prompt cache configuration.

## Diagnostics

Request dumps are disabled by default. If an upstream maintainer needs a
reproduction, enable them temporarily in the config:

```json
"dump_requests": true
```

Reproduce once, remove the dumps, and disable the option again. Dumps can
contain prompts, source code, tool arguments, and other sensitive data.
