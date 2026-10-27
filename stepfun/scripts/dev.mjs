import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const children = [];
let stopping = false;

function start(command, args, name) {
  const child = spawn(command, args, {
    cwd: root,
    env: process.env,
    stdio: "inherit",
  });
  child.on("exit", (code) => {
    if (!stopping && code && code !== 0) {
      console.error(`${name} exited with code ${code}`);
      stop(code);
    }
  });
  children.push(child);
  return child;
}

function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) {
    if (!child.killed) child.kill("SIGTERM");
  }
  setTimeout(() => process.exit(code), 500).unref();
}

start(process.execPath, ["server/index.mjs"], "api");
start(process.execPath, [resolve(root, "node_modules/vite/bin/vite.js")], "vite");

process.on("SIGINT", () => stop(0));
process.on("SIGTERM", () => stop(0));
