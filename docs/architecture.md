# Architecture

## Request path

The bridge binds only to loopback. It forwards request headers, including the
client's authorization header, to the configured upstream. It changes only the
JSON bytes needed for cache-control and missing signature fields.

## Module boundary and order

Prompt Cache and Thinking Signature are independent modules. Neither module
requires the other to be enabled. The bridge composes them in a fixed order
because both may edit the same raw JSON request:

```text
request bytes
    -> Thinking Signature.restore_request()
    -> Prompt Cache.process_request()
    -> upstream
```

Signature restoration runs first so the cache module only inserts fields around
the completed request structure. The cache module never reserializes or rewrites
thinking content. On the response path, the complete upstream response is sent
to Thinking Signature.capture_response(). Prompt Cache has no response-side
state.

Each module can be disabled independently with `enable_prompt_cache` and
`enable_thinking_signature`. Existing configurations without these keys keep
both modules enabled.

## Cache breakpoints

See [prompt-cache.md](prompt-cache.md) for the standalone Prompt Cache guide.

The current strategy places an ephemeral breakpoint at three stable boundaries:

- the final tool definition;
- the final system content block;
- the final content block of the latest user message.

Existing `cache_control` fields are left unchanged, making the operation
idempotent. The raw request is parsed only for locating structures; all edits
are applied to the original UTF-8 text.

## Signature bridge

See [thinking-signature.md](thinking-signature.md) for the standalone Thinking
Signature guide.

For each thinking block in an upstream JSON or SSE response, the bridge stores:

```text
(model, SHA-256(thinking)) -> signature
```

The SQLite database stores the model, hash, signature, expiry timestamp, and
last-touch timestamp. It does not store the thinking text. The default retention
is seven days and the default maximum is 2048 records.

When an assistant thinking block has an empty or missing signature, the bridge
looks up the same model/hash and inserts the original signature into the raw
request. If no match exists, the request is left unchanged.

## Streaming tradeoff

The bridge buffers the upstream response before forwarding it. This is
intentional: SSE signatures may be split across multiple deltas, so the bridge
needs the complete response to reconstruct the thinking block. A future version
could implement streaming capture, but that is a separate reliability tradeoff.
