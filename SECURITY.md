# Security notes

## Scope

This project is intended for a local development machine. It is not an
internet-facing gateway, authentication proxy, or multi-tenant service.

## Credentials

The installer and bridge do not accept or persist API keys. The client sends
authorization headers to the local bridge, which forwards them to the configured
upstream. Keep the bridge bound to `127.0.0.1` and protect local user access.

## Stored data

The SQLite database stores upstream-issued thinking signatures, model names,
hashes of thinking text, and timestamps. It does not store full thinking text.
Configuration and database files are created with private permissions where the
platform allows it.

Request dumps are disabled by default. Enabling them is a diagnostic action that
may persist sensitive prompts and tool data; remove dumps after use.

## Upstream trust

The configured upstream receives the full request and authorization headers.
Verify the URL before installation. Do not copy a configuration file containing
credentials into a public repository.

## Reporting

Do not include API keys, authorization headers, prompts, source code, tool
arguments, or raw request dumps in public issues. Redact logs and database
contents before sharing.
