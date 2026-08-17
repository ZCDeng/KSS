/**
 * Live Harness turn: ctx.agents.create + followup + whenIdle.
 * Chrome events are a projection of SessionEvent, not a second transcript.
 */
import { createConnection } from "node:net";
import { lstat } from "node:fs/promises";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export const OVERLAY_JSON = JSON.stringify({
  status: "succeeded",
  claims: [{ text: "Harness research node completed a KSS workspace write.", confidence: 0.7 }],
  evidence_refs: [],
  artifact_refs: ["notes.md"],
  open_questions: [],
  warnings: [],
});

const liveAgents = new Map();
let turnEmitters = new Map();
let liveDeps = null;
let currentTurnSurface = "desktop";

export function flattenMessageText(message) {
  const content = message?.content;
  if (typeof message === "string") return message;
  if (!Array.isArray(content)) return "";
  return content
    .filter((block) => block && block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("");
}

export function projectSessionEvent(event) {
  if (!event || typeof event !== "object") return null;
  const type = String(event.type || "");
  const data = event.data && typeof event.data === "object" ? event.data : {};
  if (type === "assistant/chunk") {
    const chunk = data.chunk && typeof data.chunk === "object" ? data.chunk : {};
    if (chunk.type === "text-delta" && chunk.text) {
      return { type: "message_delta", text: chunk.text, delta: chunk.text, origin: "text-delta" };
    }
    return null;
  }
  if (type === "text-chunks") {
    const texts = data.texts;
    if (Array.isArray(texts) && texts.length) {
      const text = texts.filter((part) => typeof part === "string").join("");
      if (text) return { type: "message_delta", text, delta: text, origin: "text-chunks" };
    }
    return null;
  }
  if (type === "assistant/message") {
    const text = flattenMessageText(data.message);
    return text ? { type: "chunk", text } : null;
  }
  if (type === "tool/call") {
    const name = String(data.name || data.toolName || data.tool || "");
    if (!name) return null;
    return { type: "tool_start", name, tool: name };
  }
  if (type === "tool/result") {
    const name = String(data.name || data.toolName || data.tool || "");
    return { type: "tool_end", name, tool: name };
  }
  return null;
}

export function shouldResumeCreateError(err) {
  const msg = String(err?.message || err || "");
  return /persisted log on disk|already exists/.test(msg);
}

export function approvalSessionIds(req) {
  const ids = [];
  const session = req?.agent?.session;
  for (const raw of [session?.id, session?.header?.id, req?.sessionId]) {
    const id = String(raw || "");
    if (id && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

export function resolveTurnEmitter(emitters, sessionIds) {
  const map = emitters instanceof Map ? emitters : new Map();
  for (const id of sessionIds || []) {
    if (id && map.has(id)) return map.get(id);
  }
  if (map.size === 1) return map.values().next().value;
  return undefined;
}

export async function loadLiveDeps(profileDir) {
  if (liveDeps) return liveDeps;
  const href = (pkg) =>
    pathToFileURL(join(profileDir, "node_modules/@deepseek-ai", pkg, "lib/index.js")).href;
  const llm = await import(href("dsh-llm"));
  const session = await import(href("dsh-session"));
  const agent = await import(href("dsh-agent"));
  const policy = await import(
    pathToFileURL(join(profileDir, "../kss-plugins/src/policy.js")).href
  );
  liveDeps = {
    createUserMessage: llm.createUserMessage,
    SessionId: session.SessionId,
    installModelSelection: agent.installModelSelection,
    attachSessionPolicy: policy.attachSessionPolicy,
    inheritResearchPolicy: policy.inheritResearchPolicy,
    resolveDesktopApproval: policy.resolveDesktopApproval,
    setApprovalPrompt: policy.setApprovalPrompt,
  };
  return liveDeps;
}

/**
 * Clamp one requested reasoning effort onto what the exact model supports.
 * KSS thinking_level 档位（pi-ai 词汇）在 DeepSeek 只有 off/high/max 时按
 * 桶映射收敛；模型不支持 reasoning 时丢弃，避免 UNSUPPORTED_REASONING_EFFORT。
 * @returns {Promise<string|undefined>}
 */
export async function clampReasoningEffort(ctx, provider, model, effort) {
  const requested = typeof effort === "string" ? effort.trim() : "";
  if (!requested) return undefined;
  const llm = ctx?.llm;
  if (typeof llm?.resolveModelInfo !== "function") return requested;
  let info = null;
  try {
    info = await llm.resolveModelInfo(String(provider), String(model));
  } catch {
    return undefined;
  }
  const efforts = info?.reasoning?.efforts;
  if (!Array.isArray(efforts) || efforts.length === 0) return undefined;
  const ids = efforts.map((entry) => String(entry.id));
  if (ids.includes(requested)) return requested;
  const bucket = {
    off: "off",
    minimal: "off",
    low: "off",
    medium: "high",
    high: "high",
    xhigh: "max",
    max: "max",
  }[requested];
  if (bucket && ids.includes(bucket)) return bucket;
  return info?.reasoning?.defaultEffort;
}

/** Enumerate the dsh model registry for the desktop Models page. */
export async function listHarnessModels(ctx) {
  const llm = ctx?.llm;
  const providers = [];
  if (typeof llm?.listProviders === "function") {
    for (const info of llm.listProviders()) {
      const providerId = String(info?.id || "");
      if (!providerId) continue;
      const entry = {
        id: providerId,
        name: String(info?.name || providerId),
        models: [],
      };
      let advertised = [];
      try {
        advertised = await llm.listModels(providerId);
      } catch {
        advertised = [];
      }
      for (const model of advertised || []) {
        const modelId = String(model?.id || "");
        if (!modelId) continue;
        let resolved = null;
        try {
          resolved = await llm.resolveModelInfo(providerId, modelId);
        } catch {
          resolved = null;
        }
        const modalities =
          resolved?.inputModalities ?? model?.inputModalities ?? ["text"];
        const efforts = Array.isArray(resolved?.reasoning?.efforts)
          ? resolved.reasoning.efforts.map((item) => ({
              id: String(item.id),
              name: String(item.name || item.id),
            }))
          : [];
        entry.models.push({
          provider_id: providerId,
          model_id: modelId,
          name: String(model?.name || modelId),
          description: model?.description ?? resolved?.description ?? null,
          input_modalities: Array.isArray(modalities)
            ? modalities.map(String)
            : ["text"],
          context_window: resolved?.context?.contextWindow ?? null,
          default_max_tokens: resolved?.defaultMaxTokens ?? null,
          reasoning_efforts: efforts,
          default_reasoning_effort: resolved?.reasoning?.defaultEffort ?? null,
        });
      }
      providers.push(entry);
    }
  }
  let defaultSelection = null;
  try {
    const chosen = ctx?.agentDefaultModel?.currentSelection?.();
    if (chosen?.provider && chosen?.model) {
      defaultSelection = {
        provider: String(chosen.provider),
        model: String(chosen.model),
        reasoning_effort:
          chosen.reasoningEffort === undefined ? null : String(chosen.reasoningEffort),
      };
    }
  } catch {
    defaultSelection = null;
  }
  return { providers, default_selection: defaultSelection };
}

/** Persist the default model selection through dsh settings (DSH-native path). */
export async function saveDefaultModelSelection(ctx, spec) {
  const service = ctx?.agentDefaultModel;
  if (typeof service?.saveSelection !== "function") {
    throw new Error("agentDefaultModel service unavailable");
  }
  const provider = String(spec?.provider || "");
  const model = String(spec?.model || "");
  if (!provider || !model) throw new Error("provider and model are required");
  const effort = await clampReasoningEffort(ctx, provider, model, spec?.reasoningEffort);
  await service.saveSelection({
    provider,
    model,
    ...(effort === undefined ? {} : { reasoningEffort: effort }),
  });
  return { provider, model, reasoning_effort: effort ?? null };
}

export function userMessage(deps, text) {
  return deps.createUserMessage({
    content: [{ type: "text", text: String(text || "") }],
    source: { kind: "user" },
  });
}

function providerEnvName(providerId) {
  if (providerId === "deepseek") return "DEEPSEEK_API_KEY";
  if (providerId === "openai") return "OPENAI_API_KEY";
  return "";
}

/**
 * Pull Keychain snapshot over the existing pi-ai broker socket.
 * Never writes keys to stdout, logs, or DSH_HOME files.
 * @returns {Promise<string|null>} next nonce so Python can keep the helper in sync
 */
export async function injectCredentialsFromSocket(socketPath, nonce) {
  if (typeof socketPath !== "string" || !socketPath.startsWith("/")) return null;
  if (typeof nonce !== "string" || !nonce) return null;
  if (process.env.DEEPSEEK_API_KEY || process.env.OPENAI_API_KEY) {
    return null;
  }
  const metadata = await lstat(socketPath);
  if (!metadata.isSocket()) throw new Error("credential path is not a socket");
  if ((metadata.mode & 0o077) !== 0) {
    throw new Error("credential socket permissions must be 0600");
  }
  const snapshot = await new Promise((resolve, reject) => {
    const socket = createConnection({ path: socketPath });
    let buffer = "";
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      if (error) reject(error);
      else resolve(value);
    };
    socket.setEncoding("utf8");
    socket.setTimeout(60000, () => finish(new Error("credential socket timed out")));
    socket.on("connect", () => {
      socket.write(
        `${JSON.stringify({ protocol_version: 1, action: "credentials", nonce })}\n`,
      );
    });
    socket.on("data", (chunk) => {
      buffer += chunk;
      if (buffer.length > 262144) {
        finish(new Error("credential response too large"));
        return;
      }
      const newline = buffer.indexOf("\n");
      if (newline < 0) return;
      try {
        const response = JSON.parse(buffer.slice(0, newline));
        if (response?.nonce !== nonce) throw new Error("credential nonce mismatch");
        if (!response.credentials || typeof response.credentials !== "object") {
          throw new Error("invalid credential response");
        }
        const nextNonce = response.next_nonce;
        if (typeof nextNonce !== "string" || nextNonce.length === 0) {
          throw new Error("credential response missing next nonce");
        }
        finish(undefined, { credentials: response.credentials, nextNonce });
      } catch (error) {
        finish(error);
      }
    });
    socket.on("error", (error) => finish(error));
    socket.on("timeout", () => finish(new Error("credential socket timed out")));
  });
  for (const [providerId, cred] of Object.entries(snapshot.credentials || {})) {
    if (!cred || cred.type !== "api_key") continue;
    const envName = providerEnvName(providerId);
    if (cred.key && envName && !process.env[envName]) {
      process.env[envName] = cred.key;
    }
    for (const [key, value] of Object.entries(cred.env || {})) {
      if (typeof key === "string" && typeof value === "string" && key && !process.env[key]) {
        process.env[key] = value;
      }
    }
  }
  return snapshot.nextNonce;
}

export function installStubLlm(ctx, spec = {}) {
  const overlay = spec.overlayJson || OVERLAY_JSON;
  const fallback = spec.text || "KSS live stub";
  ctx.on(
    "llm/stream",
    (options, _next) => {
      const text = currentTurnSurface === "research" ? overlay : fallback;
      return (async function* stubStream() {
        yield { type: "block-start", index: 0, blockType: "text" };
        yield { type: "text-delta", index: 0, text };
        yield { type: "block-end", index: 0, block: { type: "text", text } };
        yield { type: "finish", reason: "stop" };
      })();
    },
    { global: true, prepend: true },
  );
}

export function wireApprovalPrompt(deps) {
  deps.setApprovalPrompt((req) => {
    const emit = resolveTurnEmitter(turnEmitters, approvalSessionIds(req));
    if (!emit) {
      process.stderr.write(
        "[kss-harness-live] approval_request dropped: no turn emitter\n",
      );
      return;
    }
    emit({
      type: "approval_request",
      call_id: String(req.callId || ""),
      tool: String(req.toolName || ""),
      command: String(req.toolName || ""),
      args: req.arguments && typeof req.arguments === "object" ? req.arguments : {},
      reason: String(req.reason || ""),
    });
  });
}

export function resolveLiveApproval(deps, callId, approved) {
  deps.resolveDesktopApproval(String(callId || ""), approved ? "allowed-once" : "rejected");
}

export function abortLiveSession(sessionId, cause = "user") {
  const rec = liveAgents.get(String(sessionId || ""));
  rec?.handle?.agent?.cancel?.(cause);
}

export function steerLiveSession(deps, sessionId, text) {
  const rec = liveAgents.get(String(sessionId || ""));
  if (!rec?.handle?.agent) return false;
  rec.handle.agent.steer(userMessage(deps, text));
  return true;
}

function lastSessionEvent(handle) {
  const events = handle?.agent?.session?.events;
  if (!events || typeof events.length !== "number" || events.length < 1) return null;
  return events[events.length - 1] || null;
}

function sessionSeq(handle) {
  const session = handle?.agent?.session;
  if (typeof session?.seq === "number") return session.seq;
  const events = session?.events;
  return typeof events?.length === "number" ? events.length : 0;
}

function harvestAssistant(events, firstSeq) {
  let text = "";
  for (const event of events || []) {
    const seq = Number(event?.seq);
    if (Number.isFinite(seq) && seq <= firstSeq) continue;
    if (event?.type === "assistant/message") {
      text = flattenMessageText(event.data?.message) || text;
    } else if (event?.type === "text-chunks") {
      const projected = projectSessionEvent(event);
      if (projected?.text) text += projected.text;
    } else if (event?.type === "assistant/chunk" && !text) {
      const projected = projectSessionEvent(event);
      if (projected?.text) text += projected.text;
    }
  }
  return text;
}

function attachTurnListener(agentCtx, sessionId) {
  agentCtx.on("session/event", (session, event) => {
    const sid = String(session?.id || session?.header?.id || sessionId);
    const emit = turnEmitters.get(sid);
    const projected = projectSessionEvent(event);
    if (emit && projected) emit(projected);
  });
}

async function ensureHandle(ctx, deps, spec) {
  const sessionId = String(spec.sessionId || "");
  const existing = liveAgents.get(sessionId);
  if (existing?.handle?.agent) {
    return {
      handle: existing.handle,
      selection: existing.selection,
      reused: true,
      resumed: false,
    };
  }
  const branded = deps.SessionId(sessionId);
  const cwd = spec.cwd ? String(spec.cwd) : process.cwd();
  // 可变 selection：installModelSelection 的 agent/request waterfall 逐回合读取，
  // 让同一持久会话可以切模型/思考强度而无需重建 agent。
  const selection = { current: undefined };
  const chosen = ctx.agentDefaultModel?.currentSelection?.() || {};
  const provider = spec.provider || chosen.provider;
  const model = spec.model || chosen.model;
  const agentOptions =
    provider && model ? { provider: String(provider), model: String(model) } : undefined;
  const setup = (agentCtx) => {
    attachTurnListener(agentCtx, sessionId);
    deps.installModelSelection?.(agentCtx, selection);
  };
  const createOpts = {
    sessionId: branded,
    meta: { cwd },
    agentOptions,
    setup,
  };
  let handle;
  let resumed = false;
  try {
    handle = await ctx.agents.create(createOpts);
  } catch (err) {
    if (!shouldResumeCreateError(err) || typeof ctx.agents.resume !== "function") {
      throw err;
    }
    handle = await ctx.agents.resume({
      resumeSessionId: branded,
      agentOptions,
      setup,
    });
    resumed = true;
  }
  deps.attachSessionPolicy(handle.agent, {
    surface: spec.surface,
    owned: spec.surface === "desktop",
    allowlist:
      spec.surface === "research"
        ? { tools: spec.allowlistTools || [], cwd }
        : { tools: [], cwd: "" },
  });
  liveAgents.set(sessionId, { handle, surface: spec.surface, selection });
  return { handle, selection, reused: false, resumed };
}

/** Apply this turn's provider/model/effort onto the session's mutable selection. */
async function applyTurnSelection(ctx, selection, spec) {
  if (!selection) return;
  const fallback = ctx.agentDefaultModel?.currentSelection?.() || {};
  const provider = String(spec.provider || fallback.provider || "");
  const model = String(spec.model || fallback.model || "");
  if (!provider || !model) return;
  const effort = await clampReasoningEffort(ctx, provider, model, spec.reasoningEffort);
  selection.current = {
    provider,
    model,
    ...(effort === undefined ? {} : { reasoningEffort: effort }),
  };
}

export async function runLiveTurn(ctx, deps, spec) {
  const sessionId = String(spec.sessionId || `kss-${spec.surface}-${Date.now()}`);
  const events = [];
  let assistant = "";
  let deltaOrigin = null;
  const emit = (event) => {
    if (!event || typeof event !== "object") return;
    if (event.type === "chunk") {
      events.push(event);
      assistant = String(event.text || "");
      // Full assistant/message snapshots replace harvested text. Do not
      // forward them as chrome deltas; they duplicate streamed deltas.
      return;
    }
    if (event.type === "message_delta") {
      // Harness 可能同时给出 raw text-delta 与合批 text-chunks 两种正文流。
      // 锁定本回合首个出现的来源，丢弃另一路，避免正文翻倍。
      const origin = String(event.origin || "text-delta");
      if (deltaOrigin === null) deltaOrigin = origin;
      else if (origin !== deltaOrigin) return;
      assistant += String(event.text || event.delta || "");
    }
    events.push(event);
    if (typeof spec.onEvent === "function") spec.onEvent(event);
  };
  turnEmitters.set(sessionId, emit);
  currentTurnSurface = spec.surface === "research" ? "research" : "desktop";
  const { handle, selection, resumed } = await ensureHandle(ctx, deps, { ...spec, sessionId });
  await applyTurnSelection(ctx, selection, spec);
  try {
    emit({ type: "turn_start" });
    emit({ type: "message_start" });
    await handle.agent.whenIdle();
    const last = lastSessionEvent(handle);
    if (resumed || last?.type === "approval/asked") {
      // 故意不复投陈旧的 approval：它属于上一条早已在 UI 侧失败的请求，
      // 复投只会让新消息又卡在旧弹窗上。取消即按拒绝收口（fail-closed），
      // 然后干净地开新回合。
      handle.agent.cancel?.({ kind: "user" });
      await handle.agent.whenIdle();
    }
    const firstSeq = sessionSeq(handle);
    handle.agent.followup(userMessage(deps, spec.input || ""));
    await handle.agent.whenIdle();
    if (!assistant) {
      assistant = harvestAssistant(handle.agent.session?.events || [], firstSeq);
    }
    if (!String(assistant).trim()) {
      emit({ type: "message_end" });
      emit({ type: "turn_end" });
      return {
        ok: false,
        status: "unavailable",
        error: "empty_completion",
        assistant_text: "",
        events,
      };
    }
    if (!events.some((event) => event.type === "message_delta")) {
      emit({ type: "message_delta", text: assistant, delta: assistant });
    }
    emit({ type: "message_end" });
    emit({ type: "turn_end" });
    return {
      ok: true,
      status: "completed",
      assistant_text: assistant,
      events,
    };
  } finally {
    turnEmitters.delete(sessionId);
  }
}
