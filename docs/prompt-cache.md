# Prompt Cache

This document describes the prompt-cache part of Anthropic Cache Bridge. It is
an independent compatibility layer for Anthropic-compatible clients and
adapters; it is not tied to a particular editor, agent, or provider brand.

## Problem

Anthropic prompt caching depends on `cache_control` breakpoints being placed at
useful boundaries in the request. A client or protocol adapter may send a valid
Anthropic request without placing those breakpoints on the final tool
definition, system content, or latest user content. The request can then show
cache writes without useful cache reads, or have a lower hit rate than
expected.

This is a request-construction issue. It is independent of thinking-block
signatures and can be used without the Thinking Signature module.

## What the module changes

For an Anthropic `/v1/messages` request, the Prompt Cache module adds an
ephemeral `cache_control` object when the target object does not already have
one:

1. The final tool definition;
2. The final system block;
3. The final content block of the latest user message.

If configured, it also adds a stable `metadata.user_id` for upstream cache
affinity. Existing `cache_control` fields are not duplicated, so processing is
idempotent.

The default cache-control value is:

```json
{"type":"ephemeral","ttl":"5m"}
```

The bridge accepts `5m` and `1h` as configuration values. The upstream must
support the selected TTL; availability and billing are upstream-specific.

## Request safety

The module parses JSON only to locate the structures that need editing. It then
inserts fields into the original UTF-8 representation instead of serializing
the complete object again. This keeps unrelated content, including thinking
blocks, byte-for-byte stable.

## Enabling and disabling

The module is enabled by default:

```json
{
  "enable_prompt_cache": true
}
```

Set it to `false` when diagnosing an upstream or client problem unrelated to
cache placement. The Thinking Signature module can remain enabled or be
disabled independently.

## Verification

Use a session with a sufficiently large, repeatable prompt:

1. Send the first request so the upstream can create a cache entry;
2. Send a second request in the same session within the configured cache TTL;
3. Inspect the client usage data for a non-zero cache read value.

The cache TTL is controlled by the upstream. It is separate from the local
Thinking Signature retention period.

## Limitations

- The upstream may enforce minimum prompt sizes or other cache eligibility
  rules.
- A cache breakpoint does not guarantee a cache hit; the prompt prefix and
  routing identity must also match the upstream's rules.
- Non-Anthropic routes are forwarded without this module's changes.
- The module does not generate or validate thinking signatures.
