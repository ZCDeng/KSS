# ADR: pi-ai runs as a signed provider helper below the Python AgentRuntime

## Status

Accepted.

## Context

KSS needs pi-ai provider catalogs, capability metadata, normalized thinking and image streams, and provider-owned authentication without moving the existing session, tool, memory, Skill and research runtime into Node.

## Decision

Keep `kss/agent/` as the execution authority and run `@earendil-works/pi-ai` in a long-lived NDJSON helper.

- The release bundle pins Node 22.19.0 arm64 and pi-ai 0.82.1.
- The helper supports request-scoped abort and never executes shell commands or arbitrary modules. Node is not launched with `--jitless`: Node 22's built-in undici HTTP stack requires WebAssembly for llhttp and otherwise crashes on the first real provider request. The nested executable is signed with the minimum `allow-jit` entitlement required by the hardened runtime.
- Swift Keychain remains the durable secret store. Provider-scoped credentials are copied to the helper's in-memory `CredentialStore` over a short-lived, permission-restricted local channel.
- Python sees provider/model metadata and normalized stream events, but not persisted API-key material.
- The existing OpenAI-compatible provider remains available for one compatibility release and may be used only before any output has been emitted.

## Consequences

- pi-ai can evolve provider and model support without coupling Swift or `AgentRuntime` to vendor SDK objects.
- The app bundle grows and the nested Node executable becomes part of signing, notarization and installed-app verification.
- OAuth persistence needs a later write-back extension to the credential channel.

## Rejected

- Rewrite AgentRuntime in TypeScript — rejected because it would duplicate proven Python session, tool and research boundaries.
- Require a system Node installation — rejected because personally delivered builds must behave consistently across machines.
- Put keys in JSON, environment variables or protocol logs — rejected because Keychain is the existing security boundary.
