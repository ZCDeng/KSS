import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const catalogPath = join(dirname(fileURLToPath(import.meta.url)), "catalog.json");

export function loadPackCatalog() {
  return JSON.parse(readFileSync(catalogPath, "utf8"));
}

export function packToolMeta() {
  const tools = loadPackCatalog().map((entry) => ({
    name: entry.name,
    command: entry.command,
    write: Boolean(entry.write),
    surfaces: [...entry.surfaces],
    mcpVisible: Boolean(entry.mcpVisible),
  }));
  return { tools };
}
