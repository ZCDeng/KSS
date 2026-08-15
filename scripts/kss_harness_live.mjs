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
      return { type: "message_delta", text: chunk.text, delta: chunk.text };
    }
    return null;
  }
  if (type === "assistant/message") {
    const text = flattenMessageText(data.message);
    return text ? { type: "chunk", text } : null;
  }
  return null;
}

export async function loadLiveDeps(profileDir) {
  if (liveDeps) return liveDeps;
  const href = (pkg) =>
    pathToFileURL(join(profileDir, "node_modules/@deepseek-ai", pkg, "lib/index.js")).href;
  const llm = await import(href("dsh-llm"));
  const session = await import(href("dsh-session"));
  const policy = await import(
    pathToFileURL(join(profileDir, "../kss-plugins/src/policy.js")).href
  );
  liveDeps = {
    createUserMessage: llm.createUserMessage,
    SessionId: session.SessionId,
    attachSessionPolicy: policy.attachSessionPolicy,
    inheritResearchPolicy: policy.inheritResearchPolicy,
    resolveDesktopApproval: policy.resolveDesktopApproval,
    setApprovalPrompt: policy.setApprovalPrompt,
  };
  return liveDeps;
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
    socket.setTimeout(5000, () => finish(new Error("credential socket timed out")));
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
      const messages = Array.isArray(options?.messages) ? options.messages : [];
      const prompt = messages.map((message) => flattenMessageText(message)).join("\n");
      const text =
        prompt.includes('"status"') ||
        prompt.includes("succeeded|incomplete") ||
        prompt.includes("深度研究")
          ? overlay
          : fallback;
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
    const sessionId = String(
      req?.agent?.session?.id || req?.agent?.session?.header?.id || "",
    );
    const emit = turnEmitters.get(sessionId);
    if (!emit) return;
    emit({
      type: "approval_request",
      call_id: String(req.callId || ""),
      tool: String(req.toolName || ""),
      args: req.arguments && typeof req.arguments === "object" ? req.arguments : {},
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

async function ensureHandle(ctx, deps, spec) {
  const sessionId = String(spec.sessionId || "");
  const existing = liveAgents.get(sessionId);
  if (existing) return existing.handle;
  const branded = deps.SessionId(sessionId);
  const cwd = spec.cwd ? String(spec.cwd) : process.cwd();
  const selection = ctx.agentDefaultModel?.currentSelection?.() || {};
  const provider = spec.provider || selection.provider;
  const model = spec.model || selection.model;
  const agentOptions =
    provider && model ? { provider: String(provider), model: String(model) } : undefined;
  const handle = await ctx.agents.create({
    sessionId: branded,
    meta: { cwd },
    agentOptions,
    setup: (agentCtx) => {
      agentCtx.on("session/event", (session, event) => {
        const sid = String(session?.id || session?.header?.id || sessionId);
        const emit = turnEmitters.get(sid);
        const projected = projectSessionEvent(event);
        if (emit && projected) emit(projected);
      });
    },
  });
  deps.attachSessionPolicy(handle.agent, {
    surface: spec.surface,
    owned: spec.surface === "desktop",
    allowlist:
      spec.surface === "research"
        ? { tools: spec.allowlistTools || [], cwd }
        : { tools: [], cwd: "" },
  });
  liveAgents.set(sessionId, { handle, surface: spec.surface });
  return handle;
}

export async function runLiveTurn(ctx, deps, spec) {
  const sessionId = String(spec.sessionId || `kss-${spec.surface}-${Date.now()}`);
  const events = [];
  let assistant = "";
  const emit = (event) => {
    if (!event || typeof event !== "object") return;
    events.push(event);
    if (event.type === "chunk") {
      assistant = String(event.text || "");
    } else if (event.type === "message_delta") {
      assistant += String(event.text || event.delta || "");
    }
    if (typeof spec.onEvent === "function") spec.onEvent(event);
  };
  turnEmitters.set(sessionId, emit);
  const handle = await ensureHandle(ctx, deps, { ...spec, sessionId });
  try {
    emit({ type: "turn_start" });
    emit({ type: "message_start" });
    handle.agent.followup(userMessage(deps, spec.input || ""));
    await handle.agent.whenIdle();
    if (!assistant) {
      for (const event of handle.agent.session?.events || []) {
        if (event?.type === "assistant/message") {
          assistant = flattenMessageText(event.data?.message) || assistant;
        } else if (event?.type === "assistant/chunk" && !assistant) {
          const projected = projectSessionEvent(event);
          if (projected?.text) assistant += projected.text;
        }
      }
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
