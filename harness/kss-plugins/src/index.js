/** KSS 业务插件包：以 TOOL_SPECS 派生目录登记只读 + live 写 defineTool。 */

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createConnection } from "node:net";
import { loadPackCatalog, packToolMeta } from "./catalog.js";

export const name = "kss";
export const inject = ["tools"];
export { packToolMeta, loadPackCatalog };

function resolveDefineTool() {
  const here = dirname(fileURLToPath(import.meta.url));
  const candidate = join(
    here,
    "../../kss-profile/node_modules/@deepseek-ai/dsh-tools/lib/index.js",
  );
  return pathToFileURL(candidate).href;
}

const { defineTool } = await import(resolveDefineTool());

function defineParams(entry) {
  const params = {};
  const required = new Set(
    (entry.order || []).filter((key) => {
      if (!entry.params?.[key]) return false;
      const k = String(key);
      if (k.includes("date") || k === "args" || k.includes("limit") || k.startsWith("max_")) {
        return false;
      }
      return true;
    }),
  );
  for (const [key, schema] of Object.entries(entry.params || {})) {
    params[key] = {
      ...schema,
      ...(required.has(key) ? { required: true } : {}),
    };
  }
  return params;
}

function sidecarSocket() {
  return process.env.KSS_SIDECAR_SOCKET || "";
}

function rpc(payload) {
  const socketPath = sidecarSocket();
  if (!socketPath) {
    return Promise.reject(new Error("KSS_SIDECAR_SOCKET unset; fail-closed"));
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
    conn.write(`${JSON.stringify(payload)}\n`);
    conn.end();
  });
}

function renderJson(_args, value) {
  return [{ type: "text", text: JSON.stringify(value) }];
}

export function apply(ctx) {
  for (const entry of loadPackCatalog()) {
    ctx.tools.register(
      defineTool({
        name: entry.name,
        description: entry.desc,
        parameters: defineParams(entry),
        output: {
          schema: { type: "json" },
          render: renderJson,
        },
        isConcurrencySafe: entry.execution_mode === "parallel" ? () => true : undefined,
        async execute(args, exec) {
          const callId = String(exec?.callId || "");
          const resp = await rpc({
            cmd: "harness-tool-execute",
            name: entry.name,
            args,
            call_id: callId,
          });
          const stdout = resp?.stdout;
          if (typeof stdout === "string") {
            try {
              return JSON.parse(stdout);
            } catch {
              return { error: "bad_sidecar_stdout", stdout };
            }
          }
          return resp;
        },
      }),
    );
  }
}
