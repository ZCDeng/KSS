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

const PROTOCOL = 1;
const DRIVER = process.env.KSS_HARNESS_DRIVER === "dsh" ? "dsh" : "scripted";
const SIDECAR = process.env.KSS_SIDECAR_SOCKET || "";
const here = dirname(fileURLToPath(import.meta.url));
const profileDir = join(here, "../harness/kss-profile");

const OVERLAY_JSON = JSON.stringify({
  status: "succeeded",
  claims: [{ text: "Harness research node completed a KSS workspace write.", confidence: 0.7 }],
  evidence_refs: [],
  artifact_refs: ["notes.md"],
  open_questions: [],
  warnings: [],
});

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

async function desktopTurn(req) {
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

async function researchTurn(req) {
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

async function bootDsh() {
  const bootMod = join(profileDir, "node_modules/@deepseek-ai/dsh/lib/profile-boot-BnJoK_kl.js");
  const { runProfile } = await import(pathToFileURL(bootMod).href);
  const home = process.env.DSH_HOME;
  if (!home) {
    throw new Error("DSH_HOME is required for dsh driver");
  }
  const { ctx } = await runProfile({
    profile: "kss",
    patchFiles: [],
    args: [],
    environment: process.env,
  });
  dshCtx = ctx;
  return Boolean(ctx?.agents);
}

async function handle(msg) {
  const id = msg.id;
  const cmd = msg.cmd;
  if (cmd === "ping") {
    reply(id, { ok: true, driver: DRIVER, protocol: PROTOCOL });
    return;
  }
  if (cmd === "desktop.turn") {
    reply(id, await desktopTurn(msg));
    return;
  }
  if (cmd === "research.turn") {
    reply(id, await researchTurn(msg));
    return;
  }
  if (cmd === "abort" || cmd === "steer" || cmd === "shutdown") {
    if (cmd === "shutdown") {
      reply(id, { ok: true });
      process.exit(0);
    }
    reply(id, { ok: true });
    return;
  }
  reply(id, { ok: false, error: `unknown_cmd:${cmd}` });
}

process.stdout.write(
  `${JSON.stringify({ type: "hello", protocol: PROTOCOL, driver: DRIVER })}\n`,
);

if (DRIVER === "dsh") {
  bootDsh().catch((err) => {
    process.stderr.write(`[kss-harness-host] dsh boot failed: ${err?.stack || err}\n`);
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
