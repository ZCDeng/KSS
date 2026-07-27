#!/usr/bin/env node
/**
 * KSS pi-ai helper.
 *
 * The process owns pi-ai provider/model/auth state and exposes a deliberately
 * small NDJSON protocol on stdio. Secrets are accepted only by `auth.reload`
 * and live in memory; no command ever serializes credential values back out.
 */
import { createHash } from "node:crypto";
import { lstat } from "node:fs/promises";
import net from "node:net";
import process from "node:process";
import readline from "node:readline";

const PROTOCOL_VERSION = 1;
const PI_AI_VERSION = "0.82.1";
const mockMode = process.argv.includes("--mock");

class MemoryCredentialStore {
  constructor() {
    this.values = new Map();
    this.chains = new Map();
  }

  async read(providerId) {
    return this.values.get(providerId);
  }

  async list() {
    return [...this.values].map(([providerId, credential]) => ({
      providerId,
      type: credential.type,
    }));
  }

  async modify(providerId, fn) {
    const previous = this.chains.get(providerId) ?? Promise.resolve();
    const next = (async () => {
      await previous.catch(() => {});
      const current = this.values.get(providerId);
      const updated = await fn(current);
      if (updated !== undefined) this.values.set(providerId, updated);
      return updated ?? current;
    })();
    this.chains.set(providerId, next.catch(() => {}));
    return next;
  }

  async delete(providerId) {
    await this.modify(providerId, async () => {
      this.values.delete(providerId);
      return undefined;
    });
  }

  async replace(input) {
    const next = new Map();
    for (const [providerId, raw] of Object.entries(input ?? {})) {
      assertIdentifier(providerId, "provider id");
      if (!raw || typeof raw !== "object" || raw.type !== "api_key") {
        throw new Error(`unsupported credential for provider ${providerId}`);
      }
      const key = typeof raw.key === "string" ? raw.key : undefined;
      const env = sanitizeStringRecord(raw.env);
      if (!key && Object.keys(env).length === 0) continue;
      next.set(providerId, { type: "api_key", ...(key ? { key } : {}), ...(Object.keys(env).length ? { env } : {}) });
    }
    this.values = next;
  }

  secretValues() {
    const result = [];
    for (const credential of this.values.values()) {
      if (credential.key) result.push(credential.key);
      for (const value of Object.values(credential.env ?? {})) result.push(value);
    }
    return result.filter((value) => value.length >= 4);
  }
}

const credentials = new MemoryCredentialStore();
const activeStreams = new Map();
const customProviderFingerprints = new Map();
let models;
let pi;

async function ensurePi() {
  if (mockMode) return;
  if (models) return;
  pi = await import("@earendil-works/pi-ai");
  const { builtinModels } = await import("@earendil-works/pi-ai/providers/all");
  models = builtinModels({ credentials });
}

function write(frame) {
  process.stdout.write(`${JSON.stringify(frame)}\n`);
}

function response(requestId, result) {
  write({ type: "response", request_id: requestId, ok: true, result });
}

function failure(requestId, error, code = "helper_error") {
  write({
    type: "response",
    request_id: requestId,
    ok: false,
    error: {
      code,
      message: redact(String(error?.message ?? error)),
    },
  });
}

function streamEvent(requestId, event) {
  write({ type: "event", request_id: requestId, event });
}

function redact(message) {
  let result = message;
  for (const secret of credentials.secretValues()) {
    result = result.split(secret).join("[REDACTED]");
  }
  return result.replace(/\b(?:sk|key)-[A-Za-z0-9_-]{8,}\b/g, "[REDACTED]");
}

function sanitizeStringRecord(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === "string") result[String(key)] = item;
  }
  return result;
}

async function credentialsFromSocket(socketPath, nonce) {
  if (typeof socketPath !== "string" || !socketPath.startsWith("/")) {
    throw new Error("credential socket path must be absolute");
  }
  assertIdentifier(nonce, "credential nonce");
  const metadata = await lstat(socketPath);
  if (!metadata.isSocket()) throw new Error("credential path is not a socket");
  if ((metadata.mode & 0o077) !== 0) throw new Error("credential socket permissions must be 0600");

  return await new Promise((resolve, reject) => {
    const socket = net.createConnection({ path: socketPath });
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
      socket.write(`${JSON.stringify({ protocol_version: 1, action: "credentials", nonce })}\n`);
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
    socket.on("end", () => {
      if (!settled) finish(new Error("credential socket closed without response"));
    });
  });
}

function assertIdentifier(value, label) {
  if (typeof value !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)) {
    throw new Error(`invalid ${label}`);
  }
}

function normalizeRoute(raw) {
  const route = raw && typeof raw === "object" ? raw : {};
  assertIdentifier(route.provider_id, "provider id");
  if (typeof route.model_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$/.test(route.model_id)) {
    throw new Error("invalid model id");
  }
  if (route.base_url !== undefined && route.base_url !== null) {
    const url = new URL(route.base_url);
    if (!["http:", "https:"].includes(url.protocol)) throw new Error("base_url must use http or https");
  }
  return {
    provider_id: route.provider_id,
    model_id: route.model_id,
    base_url: route.base_url,
    thinking_level: route.thinking_level ?? "off",
    context_window: positiveInt(route.context_window, 32000),
    max_output_tokens: positiveInt(route.max_output_tokens, 8000),
    supports_images: route.supports_images !== false,
    supports_tools: route.supports_tools !== false,
    supports_thinking: route.supports_thinking === true || route.thinking_level !== "off",
  };
}

function positiveInt(value, fallback) {
  return Number.isInteger(value) && value > 0 ? value : fallback;
}

function modelRefreshOptions(value) {
  const raw = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  return {
    allowNetwork: raw.allow_network !== false,
    force: raw.force !== false,
  };
}

function modelSummary(model) {
  return {
    provider_id: model.provider,
    model_id: model.id,
    name: model.name,
    api: model.api,
    context_window: model.contextWindow,
    max_output_tokens: model.maxTokens,
    supports_images: Array.isArray(model.input) && model.input.includes("image"),
    supports_tools: true,
    supports_thinking: Boolean(model.reasoning),
  };
}

async function ensureRoute(route) {
  await ensurePi();
  if (mockMode) return {
    id: route.model_id,
    name: route.model_id,
    provider: route.provider_id,
    api: "openai-completions",
    baseUrl: route.base_url,
    reasoning: route.supports_thinking,
    input: route.supports_images ? ["text", "image"] : ["text"],
    contextWindow: route.context_window,
    maxTokens: route.max_output_tokens,
  };

  const existing = models.getModel(route.provider_id, route.model_id);
  if (existing && !route.base_url) return existing;

  const fingerprint = createHash("sha256").update(JSON.stringify(route)).digest("hex");
  if (customProviderFingerprints.get(route.provider_id) !== fingerprint) {
    const { createProvider, envApiKeyAuth } = pi;
    const { openAICompletionsApi } = await import("@earendil-works/pi-ai/api/openai-completions.lazy");
    const model = {
      id: route.model_id,
      name: route.model_id,
      api: "openai-completions",
      provider: route.provider_id,
      baseUrl: route.base_url,
      reasoning: route.supports_thinking,
      input: route.supports_images ? ["text", "image"] : ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: route.context_window,
      maxTokens: route.max_output_tokens,
    };
    models.setProvider(createProvider({
      id: route.provider_id,
      name: route.provider_id,
      ...(route.base_url ? { baseUrl: route.base_url } : {}),
      auth: { apiKey: envApiKeyAuth(`${route.provider_id} API key`, []) },
      models: [model],
      api: openAICompletionsApi(),
    }));
    customProviderFingerprints.set(route.provider_id, fingerprint);
  }
  return models.getModel(route.provider_id, route.model_id);
}

function contentBlocks(value) {
  if (typeof value === "string") return [{ type: "text", text: value }];
  if (!Array.isArray(value)) return [];
  const result = [];
  for (const block of value) {
    if (!block || typeof block !== "object") continue;
    if (block.type === "text" && typeof block.text === "string") {
      result.push({ type: "text", text: block.text });
    } else if (block.type === "image" && typeof block.data === "string" && typeof block.mimeType === "string") {
      result.push({ type: "image", data: block.data, mimeType: block.mimeType });
    } else if (block.type === "thinking" && (typeof block.thinking === "string" || typeof block.text === "string")) {
      const thinking = typeof block.thinking === "string" ? block.thinking : block.text;
      const signature = block.thinkingSignature ?? block.signature;
      result.push({ type: "thinking", thinking, ...(signature ? { thinkingSignature: signature } : {}) });
    } else if (block.type === "toolCall") {
      result.push({
        type: "toolCall",
        id: String(block.id ?? ""),
        name: String(block.name ?? ""),
        arguments: block.arguments && typeof block.arguments === "object" ? block.arguments : {},
      });
    }
  }
  return result;
}

function toPiContext(rawMessages, rawTools) {
  const system = [];
  const messages = [];
  for (const raw of Array.isArray(rawMessages) ? rawMessages : []) {
    if (!raw || typeof raw !== "object") continue;
    if (raw.role === "system") {
      const text = typeof raw.content === "string" ? raw.content : contentBlocks(raw.content).filter((b) => b.type === "text").map((b) => b.text).join("\n");
      if (text) system.push(text);
      continue;
    }
    const timestamp = Number.isFinite(raw.timestamp) ? raw.timestamp : Date.now();
    if (raw.role === "user") {
      messages.push({ role: "user", content: contentBlocks(raw.content), timestamp });
    } else if (raw.role === "assistant") {
      const content = contentBlocks(raw.content);
      for (const call of raw.tool_calls ?? []) {
        const fn = call?.function ?? {};
        let args = {};
        try {
          args = typeof fn.arguments === "string" ? JSON.parse(fn.arguments) : (fn.arguments ?? {});
        } catch {
          args = {};
        }
        content.push({ type: "toolCall", id: String(call.id ?? ""), name: String(fn.name ?? ""), arguments: args });
      }
      messages.push({
        role: "assistant",
        content,
        api: raw.api ?? "openai-completions",
        provider: raw.provider ?? "kss",
        model: raw.model ?? "unknown",
        usage: raw.usage ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
        stopReason: raw.stopReason ?? "stop",
        timestamp,
      });
    } else if (raw.role === "tool" || raw.role === "toolResult") {
      messages.push({
        role: "toolResult",
        toolCallId: String(raw.tool_call_id ?? raw.toolCallId ?? ""),
        toolName: String(raw.name ?? raw.toolName ?? "tool"),
        content: contentBlocks(raw.content),
        isError: Boolean(raw.is_error ?? raw.isError),
        timestamp,
      });
    }
  }
  const tools = [];
  for (const raw of Array.isArray(rawTools) ? rawTools : []) {
    const fn = raw?.type === "function" ? raw.function : raw;
    if (!fn || typeof fn.name !== "string") continue;
    tools.push({
      name: fn.name,
      description: typeof fn.description === "string" ? fn.description : "",
      parameters: fn.parameters && typeof fn.parameters === "object" ? fn.parameters : { type: "object", properties: {} },
    });
  }
  return {
    ...(system.length ? { systemPrompt: system.join("\n\n") } : {}),
    messages,
    ...(tools.length ? { tools } : {}),
  };
}

function usageSummary(usage) {
  if (!usage || typeof usage !== "object") return undefined;
  const input = Number(usage.input ?? usage.inputTokens ?? 0);
  const output = Number(usage.output ?? usage.outputTokens ?? 0);
  const cacheRead = Number(usage.cacheRead ?? 0);
  const cacheWrite = Number(usage.cacheWrite ?? 0);
  const reasoning = Number(usage.reasoning ?? usage.reasoningTokens ?? 0);
  return {
    input_tokens: input,
    output_tokens: output,
    total_tokens: Number(usage.totalTokens ?? input + output),
    cached_input_tokens: cacheRead || undefined,
    cache_write_tokens: cacheWrite || undefined,
    reasoning_tokens: reasoning || undefined,
  };
}

function normalizePiEvent(route, event) {
  const base = { model: route.model_id, provider: route.provider_id };
  switch (event.type) {
    case "text_start":
      return { ...base, type: "text_start", content_index: event.contentIndex };
    case "text_delta":
      return { ...base, type: "text", text: event.delta, content_index: event.contentIndex };
    case "text_end":
      return { ...base, type: "text_end", text: event.content, content_index: event.contentIndex };
    case "thinking_start":
      return { ...base, type: "thinking_start", content_index: event.contentIndex };
    case "thinking_delta":
      return { ...base, type: "thinking", text: event.delta, content_index: event.contentIndex };
    case "thinking_end": {
      const block = event.partial?.content?.[event.contentIndex];
      return {
        ...base,
        type: "thinking_end",
        text: event.content,
        content_index: event.contentIndex,
        signature: block?.thinkingSignature,
        redacted: Boolean(block?.redacted),
      };
    }
    case "toolcall_start":
      return { ...base, type: "tool_call_start", content_index: event.contentIndex };
    case "toolcall_delta":
      return { ...base, type: "tool_call_update", text: event.delta, content_index: event.contentIndex };
    case "toolcall_end":
      return {
        ...base,
        type: "tool_call",
        id: event.toolCall.id,
        name: event.toolCall.name,
        args: event.toolCall.arguments,
        content_index: event.contentIndex,
      };
    case "done":
      return {
        ...base,
        type: "finish",
        reason: event.reason,
        usage: usageSummary(event.message?.usage),
        response_id: event.message?.responseId,
      };
    case "error":
      return {
        ...base,
        type: "error",
        error: {
          code: event.reason === "aborted" ? "aborted" : "provider_error",
          message: redact(event.error?.errorMessage ?? "provider error"),
          phase: event.reason === "aborted" ? "abort" : "stream",
          retryable: false,
        },
      };
    default:
      return undefined;
  }
}

async function handleMockStream(requestId, request, route, controller) {
  const scripted = Array.isArray(request.mock_events) ? request.mock_events : null;
  const events = scripted ?? [
    { type: "text_start", content_index: 0 },
    { type: "text", text: "mock response", content_index: 0 },
    { type: "text_end", text: "mock response", content_index: 0 },
    { type: "usage", usage: { input_tokens: 3, output_tokens: 2, total_tokens: 5 } },
    { type: "finish", reason: "stop" },
  ];
  for (const event of events) {
    if (controller.signal.aborted) {
      streamEvent(requestId, {
        type: "error",
        model: route.model_id,
        provider: route.provider_id,
        error: { code: "aborted", message: "aborted", phase: "abort", retryable: false },
      });
      break;
    }
    streamEvent(requestId, { model: route.model_id, provider: route.provider_id, ...event });
    await new Promise((resolve) => setImmediate(resolve));
  }
}

async function handleStream(requestId, request) {
  const route = normalizeRoute(request.route);
  const controller = new AbortController();
  activeStreams.set(requestId, controller);
  try {
    if (mockMode) {
      await handleMockStream(requestId, request, route, controller);
      response(requestId, { completed: true });
      return;
    }
    const model = await ensureRoute(route);
    if (!model) throw new Error(`model not found: ${route.provider_id}/${route.model_id}`);
    const context = toPiContext(request.messages, request.tools);
    const options = {
      signal: controller.signal,
      temperature: typeof request.config?.temperature === "number" ? request.config.temperature : undefined,
      maxTokens: positiveInt(request.config?.max_output_tokens, route.max_output_tokens),
      timeoutMs: positiveInt(request.config?.timeout_ms, 90000),
      maxRetries: 1,
      reasoning: route.thinking_level === "off" ? undefined : route.thinking_level,
    };
    const stream = models.streamSimple(model, context, options);
    for await (const event of stream) {
      const normalized = normalizePiEvent(route, event);
      if (normalized) streamEvent(requestId, normalized);
    }
    response(requestId, { completed: true });
  } catch (error) {
    streamEvent(requestId, {
      type: "error",
      model: route.model_id,
      provider: route.provider_id,
      error: {
        code: controller.signal.aborted ? "aborted" : "provider_error",
        message: redact(error?.message ?? String(error)),
        phase: controller.signal.aborted ? "abort" : "stream",
        retryable: false,
      },
    });
    response(requestId, { completed: false });
  } finally {
    activeStreams.delete(requestId);
  }
}

async function handle(request) {
  if (!request || typeof request !== "object") throw new Error("request must be an object");
  const requestId = request.request_id;
  assertIdentifier(requestId, "request id");
  switch (request.command) {
    case "hello":
      response(requestId, {
        protocol_version: PROTOCOL_VERSION,
        pi_ai_version: PI_AI_VERSION,
        node_version: process.versions.node,
        mock: mockMode,
      });
      return;
    case "auth.reload":
      await credentials.replace(request.credentials);
      response(requestId, { credentials: await credentials.list() });
      return;
    case "auth.reload_from_socket": {
      const snapshot = await credentialsFromSocket(request.socket_path, request.nonce);
      await credentials.replace(snapshot.credentials);
      response(requestId, {
        credentials: await credentials.list(),
        next_nonce: snapshot.nextNonce,
      });
      return;
    }
    case "models.list": {
      await ensurePi();
      const providerId = typeof request.provider_id === "string"
        ? request.provider_id
        : undefined;
      const list = mockMode
        ? [{
            provider_id: "mock",
            model_id: "mock-model",
            name: "Mock Model",
            api: "mock",
            context_window: 32000,
            max_output_tokens: 8000,
            supports_images: true,
            supports_tools: true,
            supports_thinking: true,
          }]
        : models.getModels(providerId).map(modelSummary);
      response(requestId, { models: list });
      return;
    }
    case "models.refresh":
      await ensurePi();
      if (mockMode) {
        response(requestId, { refreshed: true, aborted: false, errors: [] });
        return;
      }
      {
        const result = await models.refresh(modelRefreshOptions(request.options));
        response(requestId, {
          refreshed: true,
          aborted: Boolean(result.aborted),
          errors: [...result.errors].map(([providerId, error]) => ({
            provider_id: providerId,
            message: redact(error?.message ?? String(error)),
          })),
        });
      }
      return;
    case "stream.start":
      void handleStream(requestId, request).catch((error) => failure(requestId, error));
      return;
    case "stream.abort": {
      const target = activeStreams.get(request.stream_request_id);
      if (target) target.abort(request.reason ?? "aborted");
      response(requestId, { aborted: Boolean(target) });
      return;
    }
    case "shutdown":
      for (const controller of activeStreams.values()) controller.abort("shutdown");
      response(requestId, { shutting_down: true });
      setImmediate(() => process.exit(0));
      return;
    default:
      failure(requestId, new Error(`unknown command: ${request.command}`), "unknown_command");
  }
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  if (!line.trim()) return;
  let request;
  try {
    request = JSON.parse(line);
  } catch (error) {
    write({ type: "protocol_error", error: { code: "invalid_json", message: String(error.message) } });
    return;
  }
  void handle(request).catch((error) => {
    const requestId = typeof request?.request_id === "string" ? request.request_id : "invalid";
    failure(requestId, error);
  });
});

rl.on("close", () => {
  for (const controller of activeStreams.values()) controller.abort("stdin_closed");
});
