#!/usr/bin/env bash
# CLT-friendly smoke for KSSDeck AI Chat evidence metadata.
#
# `swift test` for Tests/KSSDesktopTests requires a full Xcode XCTest runtime on
# this macOS machine.  This smoke compiles the real Foundation-only model file
# with a temporary `main.swift`, then verifies evidence payload decode + merge
# behavior without duplicating the production structs.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMPDIR="$(mktemp -d /tmp/kss-chat-evidence-smoke.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

cat > "$TMPDIR/main.swift" <<'SWIFT'
import Foundation

let data = Data("""
{
  "type": "tool_done",
  "name": "research_bundle",
  "evidenceSummary": {
    "kssTruthCount": 0,
    "externalSourceCount": 2,
    "injectionWarningCount": 1,
    "conflictCount": 1,
    "provider": "fixture"
  },
  "evidenceDrawer": {
    "kssTruth": [],
    "externalSources": [
      {
        "title": "Policy A",
        "url": "https://example.com/a",
        "sourceTier": "official_or_primary",
        "retrievedAt": "2026-06-22T00:00:00+08:00",
        "cacheStatus": "cached",
        "excerpt": "A",
        "usedFor": "external_background_only"
      }
    ],
    "warnings": [
      {"type": "prompt_injection", "severity": "danger", "message": "blocked"},
      {"type": "kss_web_conflict", "severity": "warning", "message": "KSS local truth wins"}
    ]
  }
}
""".utf8)

func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() {
        fputs("FAIL: \(message)\n", stderr)
        exit(1)
    }
}

let frame = try JSONDecoder().decode(ChatFrame.self, from: data)
expect(frame.type == "tool_done", "frame type")
expect(frame.evidenceSummary?.externalSourceCount == 2, "external count")
expect(frame.evidenceSummary?.provider == "fixture", "provider")
expect(frame.evidenceDrawer?.externalSources.first?.sourceTier == "official_or_primary", "source tier")
expect(frame.evidenceDrawer?.externalSources.first?.cacheStatus == "cached", "cache status")
expect(frame.evidenceDrawer?.warnings.map(\.type) == ["prompt_injection", "kss_web_conflict"], "warnings")

var summary = ChatEvidenceSummary(kssTruthCount: 1, externalSourceCount: 0, injectionWarningCount: 0, conflictCount: 0, provider: nil)
summary.merge(ChatEvidenceSummary(kssTruthCount: 0, externalSourceCount: 2, injectionWarningCount: 1, conflictCount: 1, provider: "fixture"))
expect(summary.kssTruthCount == 1, "kss truth merge")
expect(summary.externalSourceCount == 2, "external merge")
expect(summary.injectionWarningCount == 1, "injection merge")
expect(summary.conflictCount == 1, "conflict merge")
expect(summary.provider == "fixture", "provider merge")
expect(summary.hasEvidence, "has evidence")

var drawer = ChatEvidenceDrawer(
    kssTruth: [ChatKSSTruthEvidence(label: "get_stock", tool: "get_stock", fields: ["pctChange"], provenance: "kss_tool_truth")],
    externalSources: [],
    warnings: []
)
drawer.merge(ChatEvidenceDrawer(
    kssTruth: [],
    externalSources: [ChatExternalSourceEvidence(title: "A", url: "https://example.com/a", sourceTier: "official_or_primary", retrievedAt: "now", cacheStatus: "cached", excerpt: "A", usedFor: "external_background_only")],
    warnings: [ChatEvidenceWarning(type: "provider_unavailable", severity: "info", message: "disabled")]
))
expect(drawer.kssTruth.count == 1, "drawer kss truth merge")
expect(drawer.externalSources.count == 1, "drawer external merge")
expect(drawer.warnings.count == 1, "drawer warning merge")

print("chat evidence smoke passed")
SWIFT

swiftc \
  "$ROOT/Sources/KSSDesktop/Models/KSSModels.swift" \
  "$TMPDIR/main.swift" \
  -o "$TMPDIR/kss-chat-evidence-smoke"

"$TMPDIR/kss-chat-evidence-smoke"
