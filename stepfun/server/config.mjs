import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function readEnvFile(path) {
  if (!existsSync(path)) return {};
  const values = {};
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const index = line.indexOf("=");
    if (index === -1) continue;
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim().replace(/^["']|["']$/g, "");
    if (key) values[key] = value;
  }
  return values;
}

const fileEnv = readEnvFile(resolve(root, ".env"));

function pick(key, fallback) {
  const value = process.env[key] ?? fileEnv[key];
  return value === undefined || value === "" ? fallback : value;
}

export const config = {
  root,
  dist: resolve(root, "dist"),
  port: Number(pick("PORT", "8787")),
  dimension: 384,
  seekdb: {
    host: pick("SEEKDB_HOST", "127.0.0.1"),
    port: Number(pick("SEEKDB_PORT", "2881")),
    user: pick("SEEKDB_USER", "root"),
    password: pick("SEEKDB_PASSWORD", ""),
    database: pick("SEEKDB_DATABASE", "test"),
  },
};
