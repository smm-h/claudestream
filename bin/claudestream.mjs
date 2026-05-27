#!/usr/bin/env node
import { spawn, execFileSync } from "child_process";

// Find the Python claudestream binary
let binary;
try {
  binary = execFileSync("which", ["claudestream"], { encoding: "utf-8" }).trim();
} catch {
  // Try common paths
  for (const path of ["/usr/local/bin/claudestream", `${process.env.HOME}/.local/bin/claudestream`]) {
    try {
      execFileSync("test", ["-x", path]);
      binary = path;
      break;
    } catch { /* continue */ }
  }
}

if (!binary) {
  console.error("Error: claudestream Python package not found.");
  console.error("Install with: pip install claudestream");
  process.exit(1);
}

const child = spawn(binary, process.argv.slice(2), { stdio: "inherit" });
child.on("close", (code) => process.exit(code ?? 1));
