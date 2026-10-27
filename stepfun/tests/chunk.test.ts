import { describe, expect, it } from "vitest";

import { chunkDocument } from "../src/lib/chunk";

describe("chunkDocument", () => {
  it("returns no chunks for empty content", () => {
    expect(chunkDocument("empty.txt", "   \n\n ")).toEqual([]);
  });

  it("keeps short content as one chunk with stable metadata", () => {
    const chunks = chunkDocument("Demo Notes.md", "seekdb stores vectors and text.");
    expect(chunks).toHaveLength(1);
    expect(chunks[0].text).toContain("seekdb stores vectors");
    expect(chunks[0].metadata.title).toBe("Demo Notes.md");
    expect(chunks[0].metadata.source).toBe("upload");
    expect(chunks[0].metadata.chunkIndex).toBe(0);
    expect(chunks[0].id).toContain("demo-notes");
  });

  it("splits long content and keeps every chunk within the limit", () => {
    const paragraph = "seekdb combines full text and vector retrieval. ".repeat(80);
    const chunks = chunkDocument("long.md", `${paragraph}\n\n${paragraph}`, { maxChars: 400 });
    expect(chunks.length).toBeGreaterThan(2);
    for (const chunk of chunks) {
      expect(chunk.text.length).toBeLessThanOrEqual(400);
      expect(chunk.text.trim().length).toBeGreaterThan(0);
    }
    expect(new Set(chunks.map((chunk) => chunk.id)).size).toBe(chunks.length);
  });

  it("produces the same ids for the same input", () => {
    const first = chunkDocument("notes.md", "alpha beta\n\ngamma delta");
    const second = chunkDocument("notes.md", "alpha beta\n\ngamma delta");
    expect(first.map((chunk) => chunk.id)).toEqual(second.map((chunk) => chunk.id));
  });
});
