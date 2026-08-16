#!/usr/bin/env node
/**
 * KSS Harness Node kernel. Owns the agent turn (R1 / KTD2).
 *
 * stdin/stdout NDJSON. Python sidecar is the finance backend, not the loop owner.
 *
 * KSS_HARNESS_DRIVER=scripted  — tests / no API key: Node still decides tools
 * KSS_HARNESS_DRIVER=dsh       — boot dsh-base + kss profile, ctx.agents.create
 */
import { createConnection } from "node:net";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import readline from "node:readline";
import {
  OVERLAY_JSON,
  abortLiveSession,
  injectCredentialsFromSocket,
  installStubLlm,
  listHarnessModels,
  loadLiveDeps,
  resolveLiveApproval,
  runLiveTurn,
  saveDefaultModelSelection,
  steerLiveSession,
  wireApprovalPrompt,
} from "./kss_harness_live.mjs";

const PROTOCOL = 1;
const DRIVER = process.env.KSS_HARNESS_DRIVER === "dsh" ? "dsh" : "scripted";
const SIDECAR = process.env.KSS_SIDECAR_SOCKET || "";
const here = dirname(fileURLToPath(import.meta.url));
const profileDir = join(here, "../harness/kss-profile");

function rpc(payload) {
  if (!SIDECAR) {
    return Promise.resolve({ error: "no_socket" });
  }
  return new Promise((resolve, reject) => {
    const conn = createConnection(SIDECAR);
    let buf = "";
    conn.setEncoding("utf8");
    conn.on("error", reject);
    conn.on("data", (chunk) => {
      buf += chunk;
    });
    conn.on("end", () => {
      try {
        resolve(JSON.parse(buf.trim().split("\n")[0] || "{}"));
      } catch (err) {
        reject(err);
      }
    });
    conn.write(`${JSON.stringify(payload)}\n`);
    conn.end();
  });
}

function reply(id, body) {
  process.stdout.write(`${JSON.stringify({ id, ...body })}\n`);
}

function emitEvent(requestId, event) {
  process.stdout.write(`${JSON.stringify({ type: "event", id: requestId, event })}\n`);
}

async function scriptedDesktopTurn(req) {
  const callId = String(req.call_id || `r9-desktop-${Date.now()}`);
  const toolName = String(req.tool || "get_orientation");
  const args = req.args && typeof req.args === "object" ? req.args : {};
  if (!SIDECAR) {
    const text = `planned ${toolName}`;
    return {
      ok: true,
      status: "completed",
      assistant_text: text,
      execute: [{ name: toolName, args, call_id: callId }],
      events: [
        { type: "turn_start" },
        { type: "message_start" },
        { type: "message_delta", text, delta: text },
        { type: "chunk", text },
        { type: "message_end" },
        { type: "turn_end" },
      ],
    };
  }
  const exec = await rpc({
    cmd: "harness-tool-execute",
    name: toolName,
    args,
    call_id: callId,
  });
  const stdout = exec?.stdout;
  let result = exec;
  if (typeof stdout === "string") {
    try {
      result = JSON.parse(stdout);
    } catch {
      result = { stdout };
    }
  }
  const text =
    result?.ok === true
      ? `KSS ${toolName} ok`
      : `KSS ${toolName} ${JSON.stringify(result).slice(0, 400)}`;
  return {
    ok: true,
    status: "completed",
    assistant_text: text,
    tool_results: [result],
    events: [
      { type: "turn_start" },
      { type: "message_start" },
      { type: "message_delta", text, delta: text },
      { type: "chunk", text },
      { type: "message_end" },
      { type: "turn_end" },
    ],
  };
}

async function scriptedResearchTurn(req) {
  const cwd = String(req.cwd || "");
  if (!cwd) {
    return { ok: false, status: "interrupted", error: "research cwd unset" };
  }
  mkdirSync(cwd, { recursive: true });
  const notes = join(cwd, "notes.md");
  writeFileSync(notes, "# KSS research workspace\nHarness-owned node write.\n", "utf8");
  return {
    ok: true,
    status: "completed",
    assistant_text: OVERLAY_JSON,
    applied_write_ids: [String(req.attempt_id || "research-write")],
    workspace_file: notes,
    events: [{ type: "turn_start" }, { type: "turn_end" }],
  };
}

let dshCtx = null;
let liveDeps = null;
let bootError = null;
let credentialNextNonce = null;

async function bootDsh() {
  const stub = process.env.KSS_HARNESS_STUB_LLM === "1";
  if (stub && !process.env.DEEPSEEK_API_KEY) {
    process.env.DEEPSEEK_API_KEY = "kss-stub-placeholder";
  }
  try {
    credentialNextNonce = await injectCredentialsFromSocket(
      process.env.KSS_PI_AI_CREDENTIAL_SOCKET || "",
      process.env.KSS_PI_AI_CREDENTIAL_NONCE || "",
    );
  } catch (err) {
    process.stderr.write(
      `[kss-harness-host] credential socket skipped: ${String(err?.message || err)}\n`,
    );
  }
  const bootMod = join(profileDir, "node_modules/@deepseek-ai/dsh/lib/profile-boot-BnJoK_kl.js");
  const envMod = join(
    profileDir,
    "node_modules/@deepseek-ai/dsh-launch-environment/lib/index.js",
  );
  const { runProfile } = await import(pathToFileURL(bootMod).href);
  const { createLaunchEnvironmentSnapshot } = await import(pathToFileURL(envMod).href);
  const home = process.env.DSH_HOME;
  if (!home) {
    throw new Error("DSH_HOME is required for dsh driver");
  }
  const { ctx } = await runProfile({
    profile: "kss",
    patchFiles: [],
    args: [],
    environment: createLaunchEnvironmentSnapshot([
      { source: "process", values: process.env },
    ]),
  });
  dshCtx = ctx;
  liveDeps = await loadLiveDeps(profileDir);
  wireApprovalPrompt(liveDeps);
  if (stub) {
    installStubLlm(ctx);
  }
  return Boolean(ctx?.agents);
}

const bootPromise =
  DRIVER === "dsh"
    ? bootDsh().catch((err) => {
        bootError = err;
        process.stderr.write(
          `[kss-harness-host] dsh boot failed: ${String(err?.message || err)}\n`,
        );
        return false;
      })
    : Promise.resolve(false);

async function ensureDshReady() {
  if (DRIVER !== "dsh") return null;
  await bootPromise;
  if (bootError || !dshCtx?.agents || !liveDeps) {
    return {
      ok: false,
      status: "unavailable",
      error: "harness_session_unavailable",
      hint: "dsh agents.create is not ready",
    };
  }
  return null;
}

async function desktopTurn(req, requestId) {
  if (DRIVER === "dsh") {
    const unavailable = await ensureDshReady();
    if (unavailable) return unavailable;
    return runLiveTurn(dshCtx, liveDeps, {
      surface: "desktop",
      sessionId: String(req.session_id || `desktop-${Date.now()}`),
      input: String(req.input || ""),
      cwd: String(req.cwd || process.cwd()),
      provider: req.provider,
      model: req.model,
      reasoningEffort: req.reasoning_effort,
      onEvent: (event) => emitEvent(requestId, event),
    });
  }
  return scriptedDesktopTurn(req);
}

async function researchTurn(req, requestId) {
  if (DRIVER === "dsh") {
    const unavailable = await ensureDshReady();
    if (unavailable) {
      return { ...unavailable, status: "interrupted" };
    }
    const cwd = String(req.cwd || "");
    if (!cwd) {
      return { ok: false, status: "interrupted", error: "research cwd unset" };
    }
    mkdirSync(cwd, { recursive: true });
    const allowlist = Array.isArray(req.allowlist) ? req.allowlist.map(String) : [];
    return runLiveTurn(dshCtx, liveDeps, {
      surface: "research",
      sessionId: String(req.session_id || req.attempt_id || `research-${Date.now()}`),
      input: String(req.prompt || req.input || ""),
      cwd,
      provider: req.provider,
      model: req.model,
      reasoningEffort: req.reasoning_effort,
      allowlistTools: allowlist,
      onEvent: (event) => emitEvent(requestId, event),
    });
  }
  return scriptedResearchTurn(req);
}

async function handle(msg) {
  const id = msg.id;
  const cmd = msg.cmd;
  if (cmd === "ping") {
    reply(id, {
      ok: true,
      driver: DRIVER,
      protocol: PROTOCOL,
      agents: Boolean(dshCtx?.agents),
    });
    return;
  }
  if (cmd === "desktop.turn") {
    reply(id, await desktopTurn(msg, id));
    return;
  }
  if (cmd === "research.turn") {
    reply(id, await researchTurn(msg, id));
    return;
  }
  if (cmd === "models.list") {
    if (DRIVER !== "dsh") {
      reply(id, { ok: true, providers: [], default_selection: null });
      return;
    }
    const unavailable = await ensureDshReady();
    if (unavailable) {
      reply(id, unavailable);
      return;
    }
    try {
      reply(id, { ok: true, ...(await listHarnessModels(dshCtx)) });
    } catch (err) {
      reply(id, { ok: false, error: String(err?.message || err) });
    }
    return;
  }
  if (cmd === "models.set_default") {
    if (DRIVER !== "dsh") {
      reply(id, { ok: false, error: "models.set_default requires dsh driver" });
      return;
    }
    const unavailable = await ensureDshReady();
    if (unavailable) {
      reply(id, unavailable);
      return;
    }
    try {
      const saved = await saveDefaultModelSelection(dshCtx, {
        provider: msg.provider,
        model: msg.model,
        reasoningEffort: msg.reasoning_effort,
      });
      reply(id, { ok: true, ...saved });
    } catch (err) {
      reply(id, { ok: false, error: String(err?.message || err) });
    }
    return;
  }
  if (cmd === "confirm") {
    if (liveDeps) {
      resolveLiveApproval(liveDeps, msg.call_id, Boolean(msg.approved));
    }
    reply(id, { ok: true });
    return;
  }
  if (cmd === "abort") {
    abortLiveSession(String(msg.session_id || ""), "user");
    reply(id, { ok: true });
    return;
  }
  if (cmd === "steer") {
    if (liveDeps) {
      steerLiveSession(liveDeps, String(msg.session_id || ""), String(msg.input || ""));
    }
    reply(id, { ok: true });
    return;
  }
  if (cmd === "shutdown") {
    reply(id, { ok: true });
    process.exit(0);
  }
  reply(id, { ok: false, error: `unknown_cmd:${cmd}` });
}

process.stdout.write(
  `${JSON.stringify({ type: "hello", protocol: PROTOCOL, driver: DRIVER })}\n`,
);

if (DRIVER === "dsh") {
  bootPromise.then((agents) => {
    const ready = { type: "ready", agents: Boolean(agents && dshCtx?.agents) };
    if (typeof credentialNextNonce === "string" && credentialNextNonce) {
      ready.credential_next_nonce = credentialNextNonce;
    }
    process.stdout.write(`${JSON.stringify(ready)}\n`);
  });
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  let msg;
  try {
    msg = JSON.parse(trimmed);
  } catch {
    return;
  }
  Promise.resolve(handle(msg)).catch((err) => {
    reply(msg.id, { ok: false, error: String(err?.message || err) });
  });
});
