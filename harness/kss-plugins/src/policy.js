/** KSS 写策略：桌面 ask + 拥有者应答；研究 pre-execute 白名单。never 不得放行。 */

import { createConnection } from "node:net";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";
import { loadPackCatalog } from "./catalog.js";

const here = dirname(fileURLToPath(import.meta.url));
const { effectiveApprovalPolicy } = await import(
  pathToFileURL(
    join(here, "../../kss-profile/node_modules/@deepseek-ai/dsh-user-approval/lib/index.js"),
  ).href
);

export const name = "kss-approval-policy";
export const inject = ["tools", "approval"];

const STOCK_WRITES = new Set(["bash", "write", "edit", "str_replace_editor"]);
const policies = new WeakMap();
const pendingDesktop = new Map();

function catalogWrites() {
  return new Map(
    loadPackCatalog()
      .filter((e) => e.write)
      .map((e) => [e.name, e.command]),
  );
}

export function isWriteTool(name) {
  if (STOCK_WRITES.has(name)) return true;
  return catalogWrites().has(name);
}

export function loadResearchAllowlistStub() {
  // Conservative default: bash + in-workspace file edits only.
  // KSS live WRITE_COMMANDS are omitted; if added later they still grant then
  // dispatch via sidecar execute_harness_tool, never as cwd-local files.
  const path = join(dirname(fileURLToPath(import.meta.url)), "research-allowlist.json");
  return JSON.parse(readFileSync(path, "utf8"));
}

function freezeAllowlist(raw) {
  const tools = Object.freeze([...(raw?.tools || [])].map(String));
  const cwd = String(raw?.cwd || "");
  return Object.freeze({ tools, cwd });
}

export function attachSessionPolicy(agent, spec) {
  const surface = spec?.surface === "research" ? "research" : "desktop";
  const attached = {
    surface,
    owned: Boolean(spec?.owned),
    allowlist: freezeAllowlist(spec?.allowlist ?? (surface === "research" ? loadResearchAllowlistStub() : { tools: [], cwd: "" })),
  };
  policies.set(agent, attached);
  return attached;
}

export function inheritResearchPolicy(parent, child, escalate) {
  const parentPolicy = policies.get(parent);
  if (!parentPolicy) {
    attachSessionPolicy(child, { surface: "research", allowlist: { tools: [], cwd: "" } });
    return policies.get(child);
  }
  const inherited = freezeAllowlist(parentPolicy.allowlist);
  if (escalate) {
    // 提权请求被忽略：仍用父白名单与 cwd（KTD8）。
  }
  policies.set(child, {
    surface: "research",
    owned: false,
    allowlist: inherited,
  });
  return policies.get(child);
}

function sessionPolicyOf(agent) {
  const events = agent?.session?.events || [];
  return effectiveApprovalPolicy(events) ?? "ask";
}

function pathEscapesCwd(candidate, cwd) {
  if (!cwd) return true;
  const base = resolve(cwd);
  const target = resolve(isAbsolute(candidate) ? candidate : join(base, candidate));
  const rel = relative(base, target);
  return rel.startsWith("..") || isAbsolute(rel);
}

function cwdViolation(agent, args, allowlist, repoRoot) {
  const sessionCwd = String(agent?.session?.header?.cwd || "");
  const allowed = allowlist.cwd;
  if (!allowed) return "research write cwd is unset";
  if (repoRoot && resolve(allowed) === resolve(repoRoot)) {
    return "research cwd may not be the repository root";
  }
  if (sessionCwd && resolve(sessionCwd) !== resolve(allowed)) {
    return "research cwd does not match inherited workspace";
  }
  const hinted =
    args?.cwd ||
    args?.working_directory ||
    args?.path ||
    args?.file_path ||
    args?.target;
  if (typeof hinted === "string" && hinted && pathEscapesCwd(hinted, allowed)) {
    return "path escapes research workspace";
  }
  return "";
}

export function decidePreExecute(exec, repoRoot) {
  const name = String(exec?.name || "");
  if (!isWriteTool(name)) return { kind: "allow" };

  const agent = exec.agent;
  const attached = agent ? policies.get(agent) : undefined;
  const approvalPolicy = sessionPolicyOf(agent);

  if (approvalPolicy === "never") {
    return {
      kind: "deny",
      reason: `session approval policy is never; write "${name}" is denied (AE8)`,
    };
  }

  const surface = attached?.surface || "desktop";
  if (surface === "desktop") {
    return { kind: "ask", reason: `desktop live write "${name}" requires operator allow` };
  }

  const allowlist = attached?.allowlist || freezeAllowlist(loadResearchAllowlistStub());
  if (!allowlist.tools.includes(name)) {
    return {
      kind: "deny",
      reason: `research write "${name}" is not on the allowlist`,
    };
  }
  const cwdErr = cwdViolation(agent, exec.arguments || {}, allowlist, repoRoot);
  if (cwdErr) {
    return { kind: "deny", reason: cwdErr };
  }
  return { kind: "allow" };
}

function sidecarGrant(callId, command) {
  const socketPath = process.env.KSS_SIDECAR_SOCKET || "";
  if (!socketPath) {
    return Promise.resolve({ error: "no_socket" });
  }
  return new Promise((resolve, reject) => {
    const conn = createConnection(socketPath);
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
    conn.write(
      `${JSON.stringify({ cmd: "harness-tool-grant", call_id: callId, command })}\n`,
    );
    conn.end();
  });
}

async function grantIfKssWrite(name, callId, grantWrite) {
  const command = catalogWrites().get(name);
  if (!command || !callId) return;
  if (grantWrite) {
    await grantWrite(String(callId), command);
    return;
  }
  await sidecarGrant(String(callId), command);
}

export function resolveDesktopApproval(callId, outcome) {
  const waiter = pendingDesktop.get(String(callId));
  if (waiter) waiter(outcome);
}

export function applyKssApprovalPolicy(ctx, options = {}) {
  const repoRoot = options.repoRoot || "";
  const grantWrite = options.grantWrite;
  const answererMode = options.answererMode ?? "plugin";

  ctx.on("tools/pre-execute", async (exec, next) => {
    const decision = decidePreExecute(exec, repoRoot);
    if (decision.kind === "allow" && isWriteTool(exec.name)) {
      const attached = exec.agent ? policies.get(exec.agent) : undefined;
      if (attached?.surface === "research") {
        await grantIfKssWrite(exec.name, exec.callId, grantWrite);
      }
    }
    return decision;
  });

  if (answererMode === "none") {
    return;
  }

  ctx.on("approval/request", async (req, next) => {
    const attached = req.agent ? policies.get(req.agent) : undefined;
    if (!attached?.owned) return next();
    const callId = String(req.callId || "");
    let outcome = "unavailable";
    if (answererMode === "allow") {
      outcome = "allowed-once";
    } else if (answererMode === "reject") {
      outcome = "rejected";
    } else {
      outcome = await new Promise((resolve) => {
        pendingDesktop.set(callId, resolve);
      });
    }
    if (outcome === "allowed-once") {
      await grantIfKssWrite(req.toolName, req.callId, grantWrite);
    }
    return outcome;
  });
}

export function apply(ctx) {
  applyKssApprovalPolicy(ctx, {
    answererMode: process.env.KSS_HARNESS_ANSWERER || "plugin",
    repoRoot: process.env.KSS_REPO_ROOT || "",
  });
}
