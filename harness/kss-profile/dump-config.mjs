#!/usr/bin/env node
/**
 * Thin dump-config wrapper around the vendored dsh CLI.
 * Unmatched patch target ids are reported on stderr by upstream but still
 * exit 0; this wrapper fails loud so KSS cannot silently drop a bad overlay.
 */
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const profileDir = dirname(fileURLToPath(import.meta.url));
const dsh = join(profileDir, "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js");
const args = process.argv.slice(2);
if (!args.includes("--dump-config") && !args.includes("--dump-default-config")) {
  args.push("--dump-config");
}

const result = spawnSync(process.execPath, [dsh, ...args], {
  cwd: profileDir,
  encoding: "utf8",
  env: process.env,
});

if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);

const combined = `${result.stdout ?? ""}\n${result.stderr ?? ""}`;
const unmatched = /patch: entry "([^"]+)" not found/.test(combined);
if (unmatched) process.exit(1);
process.exit(result.status === null ? 1 : result.status);
