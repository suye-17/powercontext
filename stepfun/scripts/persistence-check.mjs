import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const baseUrl = (process.env.BASE_URL || "http://127.0.0.1:8787").replace(/\/$/, "");
const label = process.argv[2] || "check";
const collection = "stepfun_kb_int8";

async function getJson(path, init) {
  const response = await fetch(`${baseUrl}${path}`, init);
  const body = await response.json();
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status} ${body.error ?? ""}`);
  return body;
}

const stats = await getJson(`/api/stats?collection=${collection}`);
const vector = Array.from({ length: 384 }, (_, index) => Math.sin(index / 17) * 0.1);
const search = await getJson("/api/search", {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    collection,
    question: "How long is the audit log kept?",
    embedding: vector,
    limit: 5,
  }),
});

const output = {
  label,
  timestamp: new Date().toISOString(),
  baseUrl,
  stats,
  searchMode: search.mode,
  results: search.results.map((row) => ({ id: row.id, text: row.text.slice(0, 160) })),
};
const path = resolve(root, `evidence/persistence/${label}.json`);
await mkdir(dirname(path), { recursive: true });
await writeFile(path, `${JSON.stringify(output, null, 2)}\n`, "utf-8");
console.log(JSON.stringify(output, null, 2));
