# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-27
- Primary product surfaces: KSSDesktop workspaces, with Seesaw as the OpenWorker-style agent conversation and research surface.
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
- Primary navigation: startup resolves to Seesaw after the launch animation unless an explicit navigation, report deep link or recoverable session state takes precedence. The global KSS workspace rail temporarily collapses while Seesaw is active; the Seesaw header opens sessions and the narrow-window execution drawer. Skills, context and models stay with the Composer or Right Rail rather than duplicating header controls. `主题` is not a navigation destination; visual appearance remains available from the global chrome.
- Core routes/screens: empty conversation, active conversation, Models Center, Provider detail, Session Palette, Skill Palette, Context Popover, Right Rail, evidence attachment, and write confirmation.
- Content hierarchy: 53pt conversation header → centered Focus column → shared composer with pinned Skills → tool and evidence attachments → explicit safety context.

## Design principles
- Focus, not dashboard: the active prompt is the visual center. On wide windows, a compact Right Rail shows only active progress, available evidence, live market state, Skills and context; narrow windows use a trailing drawer. It must not repeat Composer or header actions.
- Skills before settings: Skills selected for the current conversation sit beside the composer. Provider configuration belongs to Seesaw's Models Center, not global KSS Settings; API keys remain in Keychain and route/explicit-test state is the only source of readiness.
- Flat by design: hairlines establish structure; cards and shadows are reserved for the Composer and content attachments.
- Preserve agency: stop, archive, enable, join/remove a session Skill, approve, and reject controls remain explicit and close to their effects.
- Tradeoffs: every theme shares a 760pt transcript and 680pt Composer measure with the same information architecture; themes may only vary visual tokens.

## Visual language
- Color: x.com tokens from `ThemeCatalog`; `#1D9BF0` is the interaction accent. Market and status semantics remain exempt.
- Typography: Chirp with HarmonyOS Sans SC cascade through `KSSFont.themed`; body 15pt, metadata 13pt, section/title 15–20pt.
- Spacing/layout rhythm: 16pt horizontal column padding, 18pt message cadence, compact 10–14pt Composer internals.
- Shape/radius/elevation: assistant text is transparent; user messages and Composer use restrained rounded surfaces; buttons/chips are capsules; charts/evidence attachments may use 12–16pt radius.
- Motion: short, interruptible state changes; no ornamental entrance animation. Respect Reduce Motion.
- Composer stability: the empty and hydrated transcript share one Composer identity; loading history must never dissolve, replace or dim the input surface.
- Utility workspaces: Skills and Context & Memory open as landscape desktop panels. They preserve the visible Composer and use a transparent outside-click surface rather than a full-window dimmer.
- Utility workspace typography: Skills and Context & Memory reuse the Settings → Tasks type scale: 14.5pt item titles, 12.5pt explanations and 11.5pt metadata. Skills is a flat x.com list: only its search field receives a capsule fill; filters use an underline tab bar and list titles/rows use hairlines rather than filled group cards. “加入本会话 / 移出本会话” selects at most three Skills for later replies in the current session; never expose the internal `pin` term as the primary action.
- Transcript readability: assistant prose renders native block Markdown. Titles, lists, quotes and market-data tables are formatted for reading; raw Markdown syntax is reserved for explicit code blocks.
- Investment report V3: static reports use a Chinese-first reading stack (`PingFang SC` before fallback), a 36pt masthead, 24pt numbered sections, 15.5pt/1.78 prose, 13.5pt compact tables and 11–12pt audit metadata. The layout is a 1200pt editorial sheet with a thin masthead rule, three-field metadata band, dense metric grid, 292pt minimum precision cards, stable section anchors and print-safe two-column cards. The private WeChat source is never copied into the repository; the compiler contract and synthetic fixture are the durable calibration source.
- Investment daily V1: the 20:00 job reads that day's PDF/HTML from Google Drive「左侧机会扫描」and writes it as the investment-analysis daily. Do not generate this page with the research harness. Desktop preview must carry `<head>` CSS into the Kami shell and must not restyle `.kss-report`.
- Imagery/iconography: SF Symbols and 36–44pt control hit areas; do not add repeated avatar chrome to assistant responses.

## Components
- Existing components to reuse: `KSSFont`, `KSSThemeTokens`, `XcomListChrome`, `EvidenceDrawerView`, `ChartWebView`, and existing confirmation views.
- New/changed components: `SeesawFocusShell`, `SeesawSessionIntro`, `SeesawTranscript`, `SeesawComposer`, responsive Right Rail, Models Center, Provider detail, Session Palette, Skill Palette, Context Popover, shared Composer, Skill starters, and compact message/tool rows.
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
- Layout adaptations: retain a centered 720–760pt maximum feed at all widths. The 340pt Right Rail is persistent at 1180pt and above, becomes a trailing drawer below that threshold, and never squeezes the Composer below its readable width.
- Touch/hover differences: pointer hover uses a 7–10% ink tint; all actions retain at least 36pt hit areas.

## Interaction states
- Loading: preserve the startup loading animation; in Seesaw, show a compact assistant-adjacent tool row with progress and tool name.
- Empty: one short prompt, a compact list of enabled Skill task rows, and the shared Composer; no hero, capability-card grid or detached recommendation link.
- Error: actionable provider/credential copy next to the Composer without destroying prior messages. Configuration readiness may only use the route, Keychain/Credential Broker and explicit connection-test state; a prior chat failure is a separate diagnostic.
- Live market: show a compact, source-labelled Longbridge snapshot in the Right Rail only when the user explicitly asks for real-time/market context. Display `as_of`, receipt time, scope and coverage limits; never imply Beijing Exchange coverage or persist live values in memory/compaction.
- Success: evidence/tool completion remains attached to the relevant message.
- Disabled: muted controls with preserved labels.
- Offline/slow network: keep existing Agent fallback and sequence-warning copy visible.

## Content voice
- Tone: direct, factual, and compact.
- Terminology: `Seesaw`, `会话`, `技能`, `记忆`, `只解释 · 不荐买卖`.
- Microcopy rules: name the action (`发送`, `停止`, `启用`, `加入本会话`); do not rely on icon-only adjacent controls.

## Implementation constraints
- Framework/styling system: native SwiftUI; no new dependencies.
- Design-token constraints: use existing semantic tokens and font cascade; no hard-coded duplicate x.com palette.
- Performance constraints: lazy timeline rendering; do not recreate the Agent/runtime layer for visual changes.
- Compatibility constraints: session, Skill and memory protocol schemas stay unchanged; provider route is an optional append-only session state so every session can select a primary model while global settings retain the default/fallback route. A session can only override its primary route; fallback is global and can run only before first output. Temporary Seesaw navigation collapse must not mutate the user's persisted sidebar preference; all safety/evidence/write-confirmation behavior must remain intact.
- Market-data constraints: Longbridge access is read-only and must reuse the shared context service/tool path. Explicit current-market intent may prefetch `market`, `watchlist` or `symbols`; historical questions must not silently trigger it. Quote provenance remains forward-observed and must be visible in the transcript/evidence rail.
- Market workspace: the rail and page title use `盯盘`. The title cluster owns the page-header market status; data freshness and coverage semantics remain unchanged and only the compact badge variant appears outside the page header.
- Investment analysis archive: `投资分析` is a read-only daily/weekly report library using the AI复盘 master-detail pattern. Rows load audit/status/hash metadata only; selecting a row opens the corresponding report artifact and deep-links research control back to the task workbench. The archive never starts, retries or publishes a research job itself.
- Test/screenshot expectations: Swift tests, Release build, and installed-app screenshots for empty conversation, active conversation, streaming/tool state, Session Palette, Skill Palette and Context Popover.

## Open questions
- [ ] Whether focused Skill detail should later support an expanded read-only resource browser without crowding the Composer.
