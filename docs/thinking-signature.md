# Thinking Signature

This document describes the thinking-signature compatibility part of Anthropic
Cache Bridge. It is intended for Cursor++ and similar clients that replay
assistant `thinking` blocks across turns but sometimes send an empty, missing,
or otherwise unusable `signature`.

## Problem

Anthropic-compatible upstreams can validate a thinking block's `signature`
against its exact `thinking` content. A client may work for the first turn, then
fail on a later turn when it replays the thinking block without the signature
that the upstream originally returned. The upstream can reject the request with
an error such as:

```text
Invalid `signature` in `thinking` block
```

This is a state-replay compatibility issue. It is separate from prompt-cache
breakpoint placement and can be used without the Prompt Cache module.

## What the module does

### Capture

After a successful upstream response, the module reads thinking blocks from
Anthropic JSON or SSE responses and stores each valid pair as:

```text
(model, SHA-256(thinking)) -> signature
```

### Restore

Before forwarding a later request, the module finds assistant thinking blocks
with an empty or missing signature. If the model and exact thinking text match
a stored entry, it inserts the previously observed signature into the original
request bytes.

If there is no matching entry, the request is left unchanged. A signature
cannot be generated locally.

### Persistence

Entries are stored in a local SQLite database so a user-service or launchd
restart does not discard the state. The default retention is seven days and the
default limit is 2048 entries. The database stores the model, thinking hash,
signature, and timestamps; it does not store the original thinking text.

## Processing order

The module is independent of Prompt Cache. Both modules may edit one request,
so the bridge uses this fixed order:

```text
original request
    -> restore missing thinking signatures
    -> inject prompt-cache breakpoints
    -> upstream
```

The cache module never reserializes the request after signature restoration.
This preserves the exact thinking content and signature representation required
by the upstream validator. On the response path, signature capture happens
after the complete upstream response has been read.

## Enabling and disabling

The module is enabled by default:

```json
{
  "enable_thinking_signature": true
}
```

Set it to `false` when using a client/upstream pair that already handles
thinking signatures correctly, or when isolating a prompt-cache issue. The
Prompt Cache module can remain enabled or be disabled independently.

## Verification

For Cursor++ or a similar thinking-enabled client:

1. Start a new session and complete a first turn;
2. Check the bridge log for `signatures_captured=1` (when the response contains
   a thinking block);
3. Restart the bridge;
4. Send the next turn in the same session;
5. Check for `signatures_restored=1` followed by `RESP 200`.

## Limitations

- Restoration requires a previously captured signature for the same model and
  exact thinking text.
- If the client changes whitespace, escaping, truncates thinking text, or
  changes the model identifier, the lookup may not match.
- An upstream can reject an old signature even when the local lookup succeeds.
- The bridge buffers responses so split SSE signature deltas can be reconstructed;
  this may reduce streaming responsiveness for very long responses.
- The module does not make prompt-cache entries or control the upstream cache
  TTL.
