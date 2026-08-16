/** KSS 业务插件包：以 TOOL_SPECS 派生目录登记只读 + live 写 defineTool。 */

import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createConnection } from "node:net";
import { loadPackCatalog, packToolMeta } from "./catalog.js";
import { apply as applyPolicy } from "./policy.js";
import { runVisionAnalysis } from "./vision.js";

export const name = "kss";
export const inject = ["tools", "approval"];
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

function jsonSafe(value) {
  return JSON.parse(
    JSON.stringify(value, (_key, item) => {
      if (typeof item === "number" && (!Number.isFinite(item) || Object.is(item, -0))) {
        return item === item ? 0 : null;
      }
      return item;
    }),
  );
}

function renderJson(_args, value) {
  return [{ type: "text", text: JSON.stringify(value) }];
}

function registerVisionTool(ctx) {
  ctx.tools.register(
    defineTool({
      name: "vision_analyze",
      description:
        "分析图片附件或工作区图片（截图/K线/报表照片）：经独立视觉模型返回 OCR 文本、版面区域与语义描述的结构化 JSON。输出是模型推断（untrusted），金融数字必须与工具数据核对后才能引用。",
      parameters: {
        attachment_id: {
          type: "string",
          description: "Seesaw 附件 id（与 path 二选一）",
        },
        path: {
          type: "string",
          description: "workspace 白名单内图片相对路径（与 attachment_id 二选一）",
        },
        intent: {
          type: "string",
          description: "想从图片得到什么，如：提取表格数字 / 识别K线形态",
        },
      },
      output: {
        schema: { type: "json" },
        render: renderJson,
      },
      isConcurrencySafe: () => true,
      async execute(args) {
        const context = await rpc({
          cmd: "harness-vision-context",
          attachment_id: String(args?.attachment_id || ""),
          path: String(args?.path || ""),
        });
        if (!context || context.ok !== true) {
          return jsonSafe({
            error: context?.error || "vision_context_unavailable",
            hint: context?.hint,
          });
        }
        return jsonSafe(
          await runVisionAnalysis({
            route: context.route,
            filePath: context.file_path,
            mediaType: context.media_type,
            intent: String(args?.intent || ""),
          }),
        );
      },
    }),
  );
}

export function apply(ctx) {
  applyPolicy(ctx);
  registerVisionTool(ctx);
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
              return jsonSafe(JSON.parse(stdout));
            } catch {
              return { error: "bad_sidecar_stdout", stdout };
            }
          }
          return jsonSafe(resp);
        },
      }),
    );
  }
}
