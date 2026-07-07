#!/usr/bin/env node
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const runtimeDir = join(repoRoot, ".agent-browser-runtime");

for (const name of ["profile", "downloads", "screenshots", "tmp"]) {
  mkdirSync(join(runtimeDir, name), { recursive: true });
}

const env = {
  ...process.env,
  AGENT_BROWSER_HOME: process.env.AGENT_BROWSER_HOME || runtimeDir,
  AGENT_BROWSER_PROFILE:
    process.env.AGENT_BROWSER_PROFILE || join(runtimeDir, "profile"),
  AGENT_BROWSER_DOWNLOAD_PATH:
    process.env.AGENT_BROWSER_DOWNLOAD_PATH || join(runtimeDir, "downloads"),
  AGENT_BROWSER_SCREENSHOT_DIR:
    process.env.AGENT_BROWSER_SCREENSHOT_DIR || join(runtimeDir, "screenshots"),
};

const bin = join(
  repoRoot,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "agent-browser.cmd" : "agent-browser",
);

const child = spawn(bin, process.argv.slice(2), {
  cwd: repoRoot,
  env,
  stdio: "inherit",
  shell: process.platform === "win32",
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
