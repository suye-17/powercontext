import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const modelId = "Xenova/all-MiniLM-L6-v2";
const revision = "751bff37182d3f1213fa05d7196b954e230abad9";
const files = ["onnx/model.onnx", "onnx/model_fp16.onnx", "onnx/model_quantized.onnx"];

const rows = [];
for (const file of files) {
  const url = `https://huggingface.co/${modelId}/resolve/${revision}/${file}`;
  const response = await fetch(url, { method: "HEAD", redirect: "follow" });
  rows.push({
    file,
    bytes: Number(response.headers.get("content-length") ?? 0),
    status: response.status,
  });
}

const output = {
  modelId,
  revision,
  measuredAt: new Date().toISOString(),
  files: rows,
};
const path = resolve(root, "evidence/model-sizes.json");
await mkdir(dirname(path), { recursive: true });
await writeFile(path, `${JSON.stringify(output, null, 2)}\n`, "utf-8");
console.log(JSON.stringify(output, null, 2));
