/**
 * modlens 风格视觉桥：纯文本主模型看不见，vision_analyze 替它看。
 *
 * 关键约束：
 * - 密钥只存在于 Node 进程环境（credential broker 注入），凭 route.api_key_env
 *   引用；Python/sidecar 只提供路径与非密钥路由元数据。
 * - 输出一律标 provenance=untrusted_model_output：OCR/语义是模型推断，
 *   金融数字必须与原图或工具数据核对后才能引用。
 */

import { readFileSync, statSync } from "node:fs";
import { extname } from "node:path";

const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MEDIA_TYPES = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

export function mediaTypeForPath(filePath, fallback = "image/png") {
  return MEDIA_TYPES[extname(String(filePath || "")).toLowerCase()] || fallback;
}

export function visionEndpoint(baseUrl) {
  const trimmed = String(baseUrl || "").replace(/\/+$/, "");
  if (!trimmed) return "";
  return trimmed.endsWith("/chat/completions")
    ? trimmed
    : `${trimmed}/chat/completions`;
}

export function buildVisionRequestBody({ model, intent, mediaType, base64 }) {
  const instruction = [
    "你是 KSS 的视觉分析桥。分析图片并只输出一个 JSON 对象（不要 Markdown 代码块），字段：",
    '"ocr_text"（图中全部可读文字，保持阅读顺序）、',
    '"layout_regions"（数组，每项 {role, text}，role 取 title/table/chart/axis/legend/toolbar/other）、',
    '"semantic_description"（图片语义描述，含图表类型与关键结构）、',
    '"warnings"（数组，标注无法确定之处）。',
    "数字必须逐字转录，不得推算、补全或四舍五入。",
  ].join("");
  const ask = intent
    ? `分析意图：${intent}`
    : "无特定意图，做完整结构化提取。";
  return {
    model: String(model || ""),
    temperature: 0,
    max_tokens: 4000,
    messages: [
      { role: "system", content: instruction },
      {
        role: "user",
        content: [
          { type: "text", text: ask },
          {
            type: "image_url",
            image_url: { url: `data:${mediaType};base64,${base64}` },
          },
        ],
      },
    ],
  };
}

export function parseVisionResponse(payload) {
  const content = payload?.choices?.[0]?.message?.content;
  const raw = typeof content === "string"
    ? content
    : Array.isArray(content)
      ? content
          .filter((block) => block && block.type === "text" && typeof block.text === "string")
          .map((block) => block.text)
          .join("")
      : "";
  const text = String(raw || "").trim();
  const unfenced = text
    .replace(/^```[a-zA-Z]*\n?/, "")
    .replace(/\n?```$/, "")
    .trim();
  try {
    const parsed = JSON.parse(unfenced);
    return {
      ocr_text: String(parsed.ocr_text || ""),
      layout_regions: Array.isArray(parsed.layout_regions) ? parsed.layout_regions : [],
      semantic_description: String(parsed.semantic_description || ""),
      warnings: Array.isArray(parsed.warnings) ? parsed.warnings.map(String) : [],
    };
  } catch {
    return {
      ocr_text: "",
      layout_regions: [],
      semantic_description: text,
      warnings: ["non_json_vision_output"],
    };
  }
}

export async function runVisionAnalysis({ route, filePath, mediaType, intent, fetchImpl }) {
  if (process.env.KSS_HARNESS_STUB_LLM === "1") {
    return {
      ok: true,
      provenance: "untrusted_model_output",
      model: String(route?.model_id || "stub"),
      evidence: {
        ocr_text: "KSS vision stub",
        layout_regions: [],
        semantic_description: `stub 分析 ${String(filePath || "")}`,
        warnings: ["stub_mode"],
      },
    };
  }
  const providerEnv = String(route?.api_key_env || "");
  const apiKey = providerEnv ? String(process.env[providerEnv] || "") : "";
  if (!apiKey) {
    return {
      error: "vision_credential_missing",
      hint: `环境变量 ${providerEnv || "(未知)"} 未注入；请在 Models 页保存该 Provider 的 API Key 后重试`,
    };
  }
  const endpoint = visionEndpoint(route?.base_url);
  if (!endpoint) return { error: "vision_base_url_missing" };
  let stat;
  try {
    stat = statSync(String(filePath || ""));
  } catch {
    return { error: "vision_file_missing", path: String(filePath || "") };
  }
  if (stat.size > MAX_IMAGE_BYTES) {
    return { error: "vision_file_too_large", max_bytes: MAX_IMAGE_BYTES };
  }
  const media = mediaType || mediaTypeForPath(filePath);
  const base64 = readFileSync(String(filePath)).toString("base64");
  const body = buildVisionRequestBody({
    model: route?.model_id,
    intent,
    mediaType: media,
    base64,
  });
  const doFetch = fetchImpl || fetch;
  let response;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort("vision timeout"), 60_000);
  try {
    response = await doFetch(endpoint, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err) {
    return { error: "vision_transport_failed", detail: String(err?.message || err) };
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    return { error: `vision_http_${response.status}` };
  }
  let payload;
  try {
    payload = await response.json();
  } catch {
    return { error: "vision_bad_response" };
  }
  return {
    ok: true,
    provenance: "untrusted_model_output",
    model: String(route?.model_id || ""),
    evidence: parseVisionResponse(payload),
  };
}
