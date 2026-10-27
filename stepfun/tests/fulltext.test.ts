import { describe, expect, it } from "vitest";

import { buildFullTextQuery } from "../server/db.mjs";

describe("buildFullTextQuery", () => {
  it("removes stopwords and SQL keywords while keeping content words", () => {
    expect(buildFullTextQuery("What is the default SQL port?")).toBe("default sql port");
    expect(buildFullTextQuery("How do you create an HNSW vector index?")).toBe(
      "hnsw vector index",
    );
    expect(buildFullTextQuery("delete collection")).toBe("collection");
  });

  it("deduplicates tokens and handles empty input", () => {
    expect(buildFullTextQuery("seekdb seekdb vector")).toBe("seekdb vector");
    expect(buildFullTextQuery("   ")).toBe("");
  });
});
