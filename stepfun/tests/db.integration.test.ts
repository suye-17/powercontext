import { afterAll, describe, expect, it } from "vitest";

import {
  closeClient,
  collectionStats,
  importItems,
  refreshCollection,
  resetCollection,
  searchCollection,
} from "../server/db.mjs";

const collection = `stepfun_test_${Date.now()}`;
const replaceCollection = `${collection}_replace`;

function vector(seed: number): number[] {
  return Array.from({ length: 384 }, (_, index) =>
    Math.sin(seed + index / 37) * 0.1,
  );
}

const items = [
  {
    id: "doc-a",
    text: "seekdb combines full text search with vector similarity search.",
    embedding: vector(1),
    metadata: { title: "hybrid", chunkIndex: 0 },
  },
  {
    id: "doc-b",
    text: "A browser can compute embeddings locally before writing to a database.",
    embedding: vector(2),
    metadata: { title: "browser", chunkIndex: 1 },
  },
  {
    id: "doc-c",
    text: "The default port for the SQL listener is 2881.",
    embedding: vector(3),
    metadata: { title: "ports", chunkIndex: 2 },
  },
];

describe.skipIf(process.env.SEEKDB_TEST !== "1")("seekdb data layer", () => {
  afterAll(async () => {
    await resetCollection(collection).catch(() => {});
    await resetCollection(replaceCollection).catch(() => {});
    await closeClient();
  });

  it("imports, searches, refreshes, and reports counts", async () => {
    await resetCollection(collection);
    const imported = await importItems(collection, items, { refresh: true });
    expect(imported.imported).toBe(3);
    expect(imported.count).toBe(3);

    const stats = await collectionStats(collection);
    expect(stats.exists).toBe(true);
    expect(stats.count).toBe(3);

    const result = await searchCollection(collection, {
      question: "full text and vector search",
      embedding: vector(1),
      limit: 3,
    });
    expect(result.mode).toBe("hybrid");
    expect(result.results.length).toBeGreaterThan(0);
    expect(result.results[0].id).toBe("doc-a");
    expect(result.results[0].text).toContain("seekdb");

    await refreshCollection(collection);
    const second = await searchCollection(collection, {
      question: "SQL listener port",
      embedding: vector(3),
      limit: 3,
    });
    expect(second.results.map((row: { id: string }) => row.id)).toContain("doc-c");
  });

  it("replaces stale chunks when a document is re-imported", async () => {
    await resetCollection(replaceCollection);
    const metadata = { documentId: "doc-x", title: "x" };
    await importItems(
      replaceCollection,
      [
        { id: "x1", text: "old alpha", embedding: vector(10), metadata: { ...metadata, chunkIndex: 0 } },
        { id: "x2", text: "old beta", embedding: vector(11), metadata: { ...metadata, chunkIndex: 1 } },
      ],
      { refresh: true },
    );
    expect((await collectionStats(replaceCollection)).count).toBe(2);

    await importItems(
      replaceCollection,
      [{ id: "x1", text: "new gamma", embedding: vector(12), metadata: { ...metadata, chunkIndex: 0 } }],
      { refresh: true },
    );
    expect((await collectionStats(replaceCollection)).count).toBe(1);
    const result = await searchCollection(replaceCollection, {
      question: "new gamma",
      embedding: vector(12),
      limit: 3,
    });
    expect(result.results.map((row: { id: string }) => row.id)).toContain("x1");
    expect(result.results.map((row: { id: string }) => row.id)).not.toContain("x2");
  });

  it("rejects invalid input", async () => {
    await expect(importItems(collection, [])).rejects.toThrow();
    await expect(
      searchCollection(collection, { question: "", embedding: vector(1), limit: 3 }),
    ).rejects.toThrow();
    await expect(
      searchCollection(collection, { question: "x", embedding: [1, 2, 3], limit: 3 }),
    ).rejects.toThrow();
  });
});
