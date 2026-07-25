# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-26
- Primary product surfaces: KSSDesktop workspaces, with Seesaw as the agent conversation and research surface.
- Evidence reviewed: `docs/plans/2026-07-11-004-feat-kssdeck-xcom-design-plan.md`, `docs/solutions/kss_desktop_swiftui_design_system.md`, `Sources/KSSDesktop/Support/Theme*.swift`, `Sources/KSSDesktop/Support/XcomListChrome.swift`, the Paper x.com reference supplied on 2026-07-26, and the installed Seesaw screenshot supplied on 2026-07-26.

## Brand
- Personality: restrained, analytical, fast, and evidence-led.
- Trust signals: explicit data provenance, current/expired status, write confirmation, visible tool state, and financial-risk copy.
- Avoid: generic AI hero pages, ornamental gradients, dashboard-card grids inside conversations, hidden labels, and decorative shadows in x.com mode.

## Product goals
- Goals: let one operator move from a market question to verified KSS evidence with minimal visual friction.
- Non-goals: imitating x.com social actions, replacing KSS financial semantic colors, or changing Agent/runtime behavior during visual work.
- Success signals: the x.com Seesaw surface reads as a research timeline at first glance; the classic themes remain behaviorally unchanged.

## Personas and jobs
- Primary personas: the repository owner operating KSSDesktop on macOS.
- User jobs: resume a conversation, ask a market question, inspect tool/evidence state, invoke a capability, manage Skills, and approve controlled writes.
- Key contexts of use: wide desktop window, information-dense research, repeated multi-turn sessions, and packaged `/Applications` builds.

## Information architecture
- Primary navigation: global KSS workspace rail, then Seesaw sessions, timeline, and optional Skills/Memory utility panel.
- Core routes/screens: empty conversation, active conversation, Skills panel, Memory panel, evidence attachment, and write confirmation.
- Content hierarchy: 53pt conversation header → composer/timeline → tool and evidence attachments → persistent safety context.

## Design principles
- Timeline, not dashboard: suggestions, capabilities, tool state, and messages use one feed-row grammar.
- Flat by design: hairlines establish structure; cards and shadows are reserved for content attachments.
- Preserve agency: stop, archive, enable, pin, approve, and reject controls remain explicit and close to their effects.
- Tradeoffs: x.com mode uses a 600pt reading column even when the surrounding workspace is wider; classic modes keep their existing wider layout.

## Visual language
- Color: x.com tokens from `ThemeCatalog`; `#1D9BF0` is the interaction accent. Market and status semantics remain exempt.
- Typography: Chirp with HarmonyOS Sans SC cascade through `KSSFont.themed`; body 15pt, metadata 13pt, section/title 15–20pt.
- Spacing/layout rhythm: 16pt horizontal feed padding, 12pt row padding, 12pt avatar/content gap.
- Shape/radius/elevation: feed rows have no radius or shadow; buttons/chips are capsules; charts/evidence attachments may use 12–16pt radius.
- Motion: short, interruptible state changes; no ornamental entrance animation. Respect Reduce Motion.
- Imagery/iconography: SF Symbols, 40pt circular identities for timeline authors, 36–44pt control hit areas.

## Components
- Existing components to reuse: `KSSFont`, `KSSThemeTokens`, `XcomListChrome`, `EvidenceDrawerView`, `ChartWebView`, and existing confirmation views.
- New/changed components: x.com Seesaw shell, timeline header, composer row, capability row, message row, session rail, and flat Skills/Memory panel.
- Variants and states: x.com and classic are separate rendering branches over the same store/runtime state.
- Token/component ownership: palette and typography remain under `ThemeCatalog`; Seesaw-specific geometry belongs with the Seesaw x.com chrome.

## Accessibility
- Target standard: WCAG AA-equivalent contrast and native macOS accessibility semantics.
- Keyboard/focus behavior: Return sends when valid; stop and destructive actions remain reachable; text fields keep visible focus.
- Contrast/readability: body and metadata use existing validated x.com tokens; labels are never hidden to save space.
- Screen-reader semantics: controls use direct Chinese labels; selected sessions expose selected state.
- Reduced motion and sensory considerations: avoid bounce and large spatial transitions; keep state changes understandable without animation.

## Responsive behavior
- Supported breakpoints/devices: macOS 14+, resizable desktop window.
- Layout adaptations: show the 320pt session rail on wide windows; retain a 600pt maximum feed; utility panels collapse before the feed becomes unreadable.
- Touch/hover differences: pointer hover uses a 7–10% ink tint; all actions retain at least 36pt hit areas.

## Interaction states
- Loading: inline timeline status with progress and tool name.
- Empty: composer first, followed by one optional suggestion and capability rows; no centered hero.
- Error: inline error content without destroying prior messages.
- Success: evidence/tool completion remains attached to the relevant message.
- Disabled: muted controls with preserved labels.
- Offline/slow network: keep existing Agent fallback and sequence-warning copy visible.

## Content voice
- Tone: direct, factual, and compact.
- Terminology: `Seesaw`, `会话`, `技能`, `记忆`, `只解释 · 不荐买卖`.
- Microcopy rules: name the action (`发送`, `停止`, `启用`, `置顶`); do not rely on icon-only adjacent controls.

## Implementation constraints
- Framework/styling system: native SwiftUI; no new dependencies.
- Design-token constraints: use existing semantic tokens and font cascade; no hard-coded duplicate x.com palette.
- Performance constraints: lazy timeline rendering; do not recreate the Agent/runtime layer for visual changes.
- Compatibility constraints: classic theme rendering and all safety/evidence/write-confirmation behavior must remain intact.
- Test/screenshot expectations: Swift tests, Release build, and installed-app screenshots for empty timeline, active conversation, and Skills panel.

## Open questions
- [ ] Whether Memory should remain a right utility panel or become a dedicated timeline route after the Skills redesign is accepted.
