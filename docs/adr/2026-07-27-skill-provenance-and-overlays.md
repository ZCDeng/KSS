# ADR: Skills use provenance, trust and protected overlay rules

## Status

Accepted.

## Context

KSS needs a richer finance Skill library without allowing third-party or user material to replace safety-critical instructions, execute scripts, or silently introduce trading recommendations.

## Decision

Extend Skill metadata with category, version, source, upstream commit, content hash, trust, required tools, allowed profiles and a protected flag.

Resolution order is protected KSS bundled Skills, adapted bundled Skills, project Skills and explicitly approved user overlays. Protected Skills cannot be shadowed. Unreviewed overlays remain disabled.

The first Vibe-derived library is curated and adapted. It retains MIT attribution, removes trade execution, position, target-price and rating instructions, and treats Skill content as methodology rather than evidence.

## Consequences

- UI and runtime can explain why a Skill is available, blocked or shadowed.
- Upstream refreshes become reviewable hash changes rather than silent replacement.

## Rejected

- Import the complete Vibe library unchanged — rejected because many Skills assume tools and advisory behavior KSS intentionally does not expose.
- Add model-writable Skill tools — rejected because autonomous instruction mutation needs a separate approval and trust design.
