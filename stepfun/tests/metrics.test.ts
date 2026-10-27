import { describe, expect, it } from "vitest";

import { average, evaluateQueries, median, ndcgAtK, recallAtK } from "../src/lib/metrics";

describe("retrieval metrics", () => {
  it("counts recall over unique relevant ids", () => {
    expect(recallAtK(["a", "b", "c"], ["a", "c"], 3)).toBe(1);
    expect(recallAtK(["x", "a"], ["a"], 1)).toBe(0);
    expect(recallAtK(["x", "a"], ["a"], 2)).toBe(1);
    expect(recallAtK(["a", "a"], ["a"], 2)).toBe(1);
  });

  it("computes nDCG with binary relevance", () => {
    expect(ndcgAtK(["a", "b", "c"], ["a", "c"], 3)).toBeCloseTo(1.5 / 1.630929, 5);
    expect(ndcgAtK(["x", "a"], ["a"], 1)).toBe(0);
    expect(ndcgAtK(["x", "a"], ["a"], 2)).toBeCloseTo(1 / Math.log2(3), 8);
  });

  it("aggregates per-query metrics", () => {
    const result = evaluateQueries(
      [
        { id: "q1", question: "one", relevant: ["a"] },
        { id: "q2", question: "two", relevant: ["b"] },
      ],
      { q1: ["a", "x"], q2: ["y", "z"] },
      5,
    );
    expect(result.recall).toBe(0.5);
    expect(result.ndcg).toBeCloseTo(0.5, 8);
    expect(result.perQuery).toHaveLength(2);
  });

  it("handles empty inputs and medians", () => {
    expect(recallAtK([], [], 5)).toBe(0);
    expect(ndcgAtK([], [], 5)).toBe(0);
    expect(average([])).toBe(0);
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
  });
});
