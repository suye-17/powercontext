import { mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { closeClient, describeCollection, executeSql } from "../server/db.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const collection = process.argv[2] || "stepfun_kb_int8";
const outputDir = resolve(root, "evidence/db");
await mkdir(outputDir, { recursive: true });

const describe = await describeCollection(collection).catch((error) => ({
  collection,
  error: error.message,
}));
const tables = await executeSql("SHOW TABLES").catch((error) => ({ error: error.message }));
const sdkCollections = await executeSql("SELECT * FROM sdk_collections").catch((error) => ({
  error: error.message,
}));

await writeFile(
  resolve(outputDir, "collection-describe.json"),
  `${JSON.stringify(describe, null, 2)}\n`,
  "utf-8",
);
await writeFile(
  resolve(outputDir, "tables.json"),
  `${JSON.stringify(tables, null, 2)}\n`,
  "utf-8",
);
await writeFile(
  resolve(outputDir, "sdk-collections.json"),
  `${JSON.stringify(sdkCollections, null, 2)}\n`,
  "utf-8",
);
console.log("collection:", JSON.stringify(describe).slice(0, 500));
console.log("tables:", JSON.stringify(tables).slice(0, 500));
await closeClient();
