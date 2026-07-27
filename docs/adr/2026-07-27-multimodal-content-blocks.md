# ADR: Agent messages persist ordered multimodal content blocks

## Status

Accepted.

## Context

Plain string messages cannot represent provider thinking, interleaved stream blocks, images, durable attachment references or provider continuity signatures.

## Decision

Represent message content as ordered blocks while preserving a text compatibility view.

- Supported blocks are text, thinking, image, attachment reference and tool call.
- Stream blocks are identified by `content_index`; existing text events continue for protocol-v1 clients.
- Provider thinking is persisted for hydration and same-provider continuity, but is excluded from memory, compaction summaries, Skills and research evidence.
- Attachments are content-addressed. Session JSONL stores metadata and object IDs, never embedded file bytes.
- User chat attachments do not become research evidence unless the research ledger imports and verifies them separately.

## Consequences

- Old string sessions continue to load as one text block.
- Provider changes strip incompatible opaque signatures instead of replaying them as ordinary prompt text.
- Context accounting must include extracted attachment text and image capability checks.

## Rejected

- Store base64 attachments in session JSONL — rejected because it makes recovery and compaction unbounded.
- Flatten thinking into assistant text — rejected because it destroys block order and can misrepresent provider output.
