# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-27
- Primary product surfaces: KSSDesktop workspaces, with Seesaw as the agent conversation and research surface.
- Evidence reviewed: `docs/plans/2026-07-11-004-feat-kssdeck-xcom-design-plan.md`, `docs/solutions/kss_desktop_swiftui_design_system.md`, `Sources/KSSDesktop/Support/Theme*.swift`, `Sources/KSSDesktop/Support/XcomListChrome.swift`, the Paper x.com reference supplied on 2026-07-26, the installed Seesaw screenshots supplied on 2026-07-26, OpenWorker, and AI SDK Agents Chat Grok.

## Brand
- Personality: restrained, analytical, fast, and evidence-led.
- Trust signals: explicit data provenance, current/expired status, write confirmation, visible tool state, and financial-risk copy.
- Avoid: generic AI hero pages, ornamental gradients, dashboard-card grids inside conversations, hidden labels, and decorative shadows in x.com mode.

## Product goals
- Goals: let one operator move from a market question to verified KSS evidence with minimal visual friction.
- Non-goals: imitating x.com social actions, replacing KSS financial semantic colors, or changing Agent/runtime behavior during visual work.
- Success signals: the active question and pinned Skills are visible without scanning a three-column workspace; session, memory and provider operations remain reachable without reducing the conversation column.

## Personas and jobs
- Primary personas: the repository owner operating KSSDesktop on macOS.
- User jobs: resume a conversation, ask a market question, inspect tool/evidence state, invoke a capability, manage Skills, and approve controlled writes.
- Key contexts of use: wide desktop window, information-dense research, repeated multi-turn sessions, and packaged `/Applications` builds.

## Information architecture
- Primary navigation: global KSS workspace rail temporarily collapses while Seesaw is active; the Seesaw header opens sessions, Skills and context as on-demand overlays.
- Core routes/screens: empty conversation, active conversation, Session Palette, Skill Palette, Context Popover, evidence attachment, and write confirmation.
- Content hierarchy: 53pt conversation header → centered Focus column → shared composer with pinned Skills → tool and evidence attachments → explicit safety context.

## Design principles
- Focus, not dashboard: the active prompt is the visual center; session, memory and provider management are transient overlays.
- Skills before settings: pinned Skills sit beside the composer, while provider configuration is shown only as compact status or actionable error copy.
- Flat by design: hairlines establish structure; cards and shadows are reserved for the Composer and content attachments.
- Preserve agency: stop, archive, enable, pin, approve, and reject controls remain explicit and close to their effects.
- Tradeoffs: every theme shares a 760pt maximum Focus column and the same information architecture; themes may only vary visual tokens.

## Visual language
- Color: x.com tokens from `ThemeCatalog`; `#1D9BF0` is the interaction accent. Market and status semantics remain exempt.
- Typography: Chirp with HarmonyOS Sans SC cascade through `KSSFont.themed`; body 15pt, metadata 13pt, section/title 15–20pt.
- Spacing/layout rhythm: 16pt horizontal column padding, 18pt message cadence, compact 10–14pt Composer internals.
- Shape/radius/elevation: assistant text is transparent; user messages and Composer use restrained rounded surfaces; buttons/chips are capsules; charts/evidence attachments may use 12–16pt radius.
- Motion: short, interruptible state changes; no ornamental entrance animation. Respect Reduce Motion.
- Imagery/iconography: SF Symbols and 36–44pt control hit areas; do not add repeated avatar chrome to assistant responses.

## Components
- Existing components to reuse: `KSSFont`, `KSSThemeTokens`, `XcomListChrome`, `EvidenceDrawerView`, `ChartWebView`, and existing confirmation views.
- New/changed components: shared Focus shell, Session Palette, Skill Palette, Context Popover, shared Composer, Skill starters, and compact message/tool rows.
- Variants and states: x.com and classic share one rendering hierarchy over the same store/runtime state; only their visual tokens differ.
- Token/component ownership: palette and typography remain under `ThemeCatalog`; Seesaw-specific geometry belongs with the shared Seesaw chrome.

## Accessibility
- Target standard: WCAG AA-equivalent contrast and native macOS accessibility semantics.
- Keyboard/focus behavior: Return sends when valid; Option+Return queues a follow-up during generation; palettes close on Escape; selecting a session or starter returns focus to the Composer.
- Contrast/readability: body and metadata use existing validated x.com tokens; labels are never hidden to save space.
- Screen-reader semantics: controls use direct Chinese labels; selected sessions expose selected state.
- Reduced motion and sensory considerations: avoid bounce and large spatial transitions; keep state changes understandable without animation.

## Responsive behavior
- Supported breakpoints/devices: macOS 14+, resizable desktop window.
- Layout adaptations: retain a centered 760pt maximum feed at all widths; auxiliary operations use popovers rather than persistent rails; labels compact before the conversation width is reduced.
- Touch/hover differences: pointer hover uses a 7–10% ink tint; all actions retain at least 36pt hit areas.

## Interaction states
- Loading: a compact assistant-adjacent tool row with progress and tool name.
- Empty: brief prompt, shared Composer, enabled Skill starters, then optional suggestions; no hero or capability-card grid.
- Error: actionable provider/credential copy next to the Composer without destroying prior messages.
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
- Compatibility constraints: session, Skill and memory protocol schemas stay unchanged; temporary Seesaw navigation collapse must not mutate the user's persisted sidebar preference; all safety/evidence/write-confirmation behavior must remain intact.
- Test/screenshot expectations: Swift tests, Release build, and installed-app screenshots for empty conversation, active conversation, streaming/tool state, Session Palette, Skill Palette and Context Popover.

## Open questions
- [ ] Whether focused Skill detail should later support an expanded read-only resource browser without crowding the Composer.
