import { SeekdbClient } from "seekdb";

import { config } from "./config.mjs";

const COLLECTION_CONFIG = {
  hnsw: { dimension: config.dimension, distance: "cosine" },
  fulltextConfig: { analyzer: "space" },
};

const COLLECTION_NAME = /^[A-Za-z0-9_]{1,64}$/;

const STOPWORDS = new Set(
  (
    "a about after also an and any are as at be been before between but by can could did do " +
    "does doing for from had has have having he her here hers him his how i if in into is it " +
    "its just may me might more most must my no nor not of on once only or other our out over " +
    "own same shall she should so some such than that the their them then there these they " +
    "this those through to too under until up use used using very was we were what when where " +
    "which while who whom why will with without would you your"
  ).split(" "),
);

const SQL_KEYWORDS = new Set(
  (
    "alter begin call commit create delete describe drop explain grant insert lock merge " +
    "replace revoke rollback select set show start truncate unlock update"
  ).split(" "),
);

export function buildFullTextQuery(question) {
  const tokens = String(question ?? "")
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  const kept = [];
  for (const token of tokens) {
    if (token.length < 2) continue;
    if (STOPWORDS.has(token)) continue;
    if (SQL_KEYWORDS.has(token)) continue;
    if (!kept.includes(token)) kept.push(token);
  }
  return kept.slice(0, 18).join(" ");
}

let client = null;
let connecting = null;

export class ValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "ValidationError";
    this.status = 400;
  }
}

export function getDbInfo() {
  return {
    host: config.seekdb.host,
    port: config.seekdb.port,
    database: config.seekdb.database,
    dimension: config.dimension,
  };
}

async function getClient() {
  if (client) return client;
  if (!connecting) {
    const candidate = new SeekdbClient({ ...config.seekdb });
    connecting = candidate
      .execute("SELECT 1")
      .then(() => {
        client = candidate;
        return client;
      })
      .catch(async (error) => {
        connecting = null;
        await candidate.close().catch(() => {});
        throw error;
      });
  }
  return connecting;
}

export async function closeClient() {
  if (client) {
    const current = client;
    client = null;
    connecting = null;
    await current.close().catch(() => {});
  }
}

function assertCollectionName(name) {
  if (typeof name !== "string" || !COLLECTION_NAME.test(name)) {
    throw new ValidationError("collection 名称只能包含字母、数字和下划线，长度不超过 64");
  }
}

function normalizeEmbedding(value, dimension = config.dimension) {
  if (!Array.isArray(value) || value.length !== dimension) {
    throw new ValidationError(`embedding 必须是长度为 ${dimension} 的数组`);
  }
  for (const item of value) {
    if (typeof item !== "number" || !Number.isFinite(item)) {
      throw new ValidationError("embedding 只能包含有限数值");
    }
  }
  return value;
}

function sanitizeMetadata(value) {
  if (value === undefined || value === null) return {};
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new ValidationError("metadata 必须是对象");
  }
  const output = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === undefined || item === null) continue;
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
      output[key] = item;
    } else if (Array.isArray(item)) {
      output[key] = item.filter(
        (entry) =>
          typeof entry === "string" ||
          typeof entry === "number" ||
          typeof entry === "boolean",
      );
    } else {
      throw new ValidationError(`metadata.${key} 只能是原始值或原始值数组`);
    }
  }
  return output;
}

function normalizeItem(item, index) {
  if (!item || typeof item !== "object") {
    throw new ValidationError(`items[${index}] 必须是对象`);
  }
  const id = String(item.id ?? "").trim();
  if (!id || id.length > 512) {
    throw new ValidationError(`items[${index}].id 不能为空且长度不能超过 512`);
  }
  const text = String(item.text ?? "");
  if (!text.trim()) {
    throw new ValidationError(`items[${index}].text 不能为空`);
  }
  const embedding = normalizeEmbedding(item.embedding);
  const metadata = sanitizeMetadata(item.metadata);
  return { id, text, embedding, metadata };
}

async function getCollection(name) {
  assertCollectionName(name);
  const current = await getClient();
  if (!(await current.hasCollection(name))) {
    await current.createCollection({
      name,
      configuration: COLLECTION_CONFIG,
      embeddingFunction: null,
    });
  }
  try {
    return await current.getCollection({ name, embeddingFunction: null });
  } catch {
    return current.getCollection({ name });
  }
}

function mapResults(payload) {
  const ids = payload?.ids?.[0] ?? [];
  const documents = payload?.documents?.[0] ?? [];
  const metadatas = payload?.metadatas?.[0] ?? [];
  const distances = payload?.distances?.[0] ?? [];
  return ids.map((id, index) => ({
    id: String(id),
    text: typeof documents[index] === "string" ? documents[index] : "",
    metadata: metadatas[index] ?? {},
    distance: typeof distances[index] === "number" ? distances[index] : null,
    rank: index + 1,
  }));
}

export async function importItems(collectionName, items, options = {}) {
  assertCollectionName(collectionName);
  const refresh = options.refresh !== false;
  if (!Array.isArray(items) || items.length === 0) {
    throw new ValidationError("items 不能为空");
  }
  if (items.length > 512) {
    throw new ValidationError("单次导入不能超过 512 条");
  }
  const normalized = items.map(normalizeItem);
  const collection = await getCollection(collectionName);
  const started = performance.now();
  const documentIds = [
    ...new Set(
      normalized
        .map((item) => item.metadata.documentId)
        .filter((value) => typeof value === "string" && value.length > 0),
    ),
  ];
  for (const documentId of documentIds) {
    await collection.delete({ where: { documentId: { $eq: documentId } } });
  }
  await collection.upsert({
    ids: normalized.map((item) => item.id),
    embeddings: normalized.map((item) => item.embedding),
    documents: normalized.map((item) => item.text),
    metadatas: normalized.map((item) => item.metadata),
  });
  if (refresh) await collection.refresh_index();
  const count = await collection.count();
  return {
    collection: collectionName,
    imported: normalized.length,
    count,
    refreshed: refresh,
    durationMs: Math.round(performance.now() - started),
  };
}

export async function searchCollection(collectionName, payload) {
  assertCollectionName(collectionName);
  const question = String(payload?.question ?? "").trim();
  if (!question || question.length > 2000) {
    throw new ValidationError("question 不能为空且长度不能超过 2000");
  }
  const embedding = normalizeEmbedding(payload?.embedding);
  const rawLimit = Number(payload?.limit ?? 10);
  const limit = Math.min(50, Math.max(1, Number.isFinite(rawLimit) ? Math.trunc(rawLimit) : 10));
  const collection = await getCollection(collectionName);
  const candidates = Math.max(limit * 4, 40);
  const fullTextQuery = buildFullTextQuery(question);
  const started = performance.now();
  let mode = fullTextQuery ? "hybrid" : "vector-fallback";
  let result;
  try {
    result = await collection.hybridSearch({
      ...(fullTextQuery
        ? { query: { whereDocument: { $contains: fullTextQuery }, nResults: candidates } }
        : {}),
      knn: { queryEmbeddings: embedding, nResults: candidates },
      rank: { rrf: {} },
      nResults: limit,
      include: ["documents", "metadatas", "distances"],
    });
  } catch (error) {
    mode = "vector-fallback";
    result = await collection.query({
      queryEmbeddings: embedding,
      nResults: limit,
      include: ["documents", "metadatas", "distances"],
    });
  }
  return {
    collection: collectionName,
    mode,
    fullTextQuery,
    results: mapResults(result),
    durationMs: Math.round(performance.now() - started),
  };
}

export async function refreshCollection(collectionName) {
  assertCollectionName(collectionName);
  const collection = await getCollection(collectionName);
  const started = performance.now();
  await collection.refresh_index();
  return { collection: collectionName, refreshed: true, durationMs: Math.round(performance.now() - started) };
}

export async function resetCollection(collectionName) {
  assertCollectionName(collectionName);
  const current = await getClient();
  if (await current.hasCollection(collectionName)) {
    await current.deleteCollection(collectionName);
  }
  const collection = await getCollection(collectionName);
  return { collection: collectionName, reset: true, count: await collection.count() };
}

export async function collectionStats(collectionName) {
  assertCollectionName(collectionName);
  const current = await getClient();
  if (!(await current.hasCollection(collectionName))) {
    return { collection: collectionName, exists: false, count: 0 };
  }
  const collection = await current.getCollection({ name: collectionName, embeddingFunction: null });
  return { collection: collectionName, exists: true, count: await collection.count() };
}

export async function listCollectionStats() {
  const current = await getClient();
  const collections = await current.listCollections({ withEmbeddingFunction: false });
  const rows = [];
  for (const collection of collections) {
    rows.push({
      collection: collection.name,
      count: await collection.count().catch(() => 0),
    });
  }
  return rows.sort((left, right) => left.collection.localeCompare(right.collection));
}

export async function describeCollection(collectionName) {
  assertCollectionName(collectionName);
  const current = await getClient();
  if (!(await current.hasCollection(collectionName))) {
    return { collection: collectionName, exists: false };
  }
  const collection = await current.getCollection({ name: collectionName, embeddingFunction: null });
  const metadata = await collection.describe();
  return { collection: collectionName, exists: true, metadata };
}

export async function executeSql(sql) {
  const current = await getClient();
  return current.execute(sql);
}
