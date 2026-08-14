/**
 * U3 策略评测入口：用 vendored Cordis / dsh-tools / dsh-user-approval 跑一次工具调用。
 * pytest 以 JSON 入参调用；不启动完整 agent loop。
 */
import { createConnection } from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  applyKssApprovalPolicy,
  attachSessionPolicy,
  inheritResearchPolicy,
} from "./policy.js";

const here = dirname(fileURLToPath(import.meta.url));
const nm = join(here, "../../kss-profile/node_modules/@deepseek-ai");
const href = (pkg, file = "lib/index.js") => pathToFileURL(join(nm, pkg, file)).href;
const { Context } = await import(href("cordis"));
const { CallId } = await import(href("dsh-llm"));
const { Session, SessionId } = await import(href("dsh-session"));
const { default: SystemPrompt } = await import(href("dsh-system-prompt"));
const toolsMod = await import(href("dsh-tools"));
const { default: ToolRuntime, defineTool } = toolsMod;
const approvalMod = await import(href("dsh-user-approval"));
const { default: ApprovalService, setApprovalPolicy } = approvalMod;

function rpcGrant(payload) {
  const socketPath = process.env.KSS_SIDECAR_SOCKET || "";
  if (!socketPath) return Promise.resolve({ error: "no_socket" });
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
    conn.write(`${JSON.stringify(payload)}\n`);
    conn.end();
  });
}

function dummyTool(name, ran) {
  return defineTool({
    name,
    description: name,
    parameters: {
      path: { type: "string" },
      cwd: { type: "string" },
    },
    output: {
      schema: { type: "json" },
      render: () => [{ type: "text", text: "ok" }],
    },
    async execute() {
      ran.push(name);
      return { wrote: true, name };
    },
  });
}

function makeAgent(id, { cwd, policy } = {}) {
  const header = {
    version: 0,
    id: SessionId(id),
    createdAt: Date.now(),
    ...(cwd ? { cwd } : {}),
  };
  const session = Session.create(SessionId(id), undefined, header);
  session.append("turn/start", { turn: 1 });
  if (policy) setApprovalPolicy(session, policy);
  return { id, session };
}

async function runScenario(spec) {
  const ctx = new Context();
  await ctx.plugin(SystemPrompt);
  await ctx.plugin(ToolRuntime);
  await ctx.plugin(ApprovalService, { policy: spec.sessionPolicy ?? "ask" });

  const grants = [];
  const grantWrite = async (callId, command) => {
    grants.push({ callId, command });
    if (spec.grantViaSidecar) {
      await rpcGrant({
        cmd: "harness-grant-write",
        call_id: callId,
        command,
      });
    }
  };

  applyKssApprovalPolicy(ctx, {
    grantWrite,
    answererMode: spec.answererMode ?? "none",
    repoRoot: spec.repoRoot || join(here, "../../.."),
  });

  const ran = [];
  ctx.tools.register(dummyTool("bash", ran));
  ctx.tools.register(dummyTool("write", ran));
  ctx.tools.register(dummyTool("run_task", ran));
  ctx.tools.register(dummyTool("get_orientation", ran));

  const parent = makeAgent(spec.agentId || "agent-parent", {
    cwd: spec.cwd,
    policy: spec.sessionPolicy,
  });
  attachSessionPolicy(parent, {
    surface: spec.surface || "research",
    allowlist: spec.allowlist,
    owned: Boolean(spec.owned),
  });

  let agent = parent;
  if (spec.asChild) {
    const child = makeAgent(spec.childId || "agent-child", {
      cwd: spec.childCwd ?? spec.cwd,
      policy: spec.childPolicy ?? spec.sessionPolicy,
    });
    inheritResearchPolicy(parent, child, spec.childEscalate);
    agent = child;
  }

  const result = await ctx.tools.execute({
    callId: CallId(spec.callId || "call-1"),
    name: spec.tool,
    arguments: spec.args || {},
    agent,
    signal: AbortSignal.timeout(15_000),
  });

  const events = agent.session.events.map((e) => e.type);
  return {
    isError: Boolean(result.isError),
    error: result.error?.message || "",
    content: (result.content || []).map((c) => c.text).join("\n"),
    bodyRan: ran.includes(spec.tool),
    grants,
    asked: events.includes("approval/asked"),
    decided: events.includes("approval/decided"),
    decisionOutcomes: agent.session.events
      .filter((e) => e.type === "approval/decided")
      .map((e) => e.data.outcome),
  };
}

const spec = JSON.parse(process.argv[2] || "{}");
const out = await runScenario(spec);
process.stdout.write(`${JSON.stringify(out)}\n`);
