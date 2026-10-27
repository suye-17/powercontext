import { afterEach, describe, expect, it, vi } from "vitest";

import { detectWebGPU } from "../src/lib/embeddings";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("detectWebGPU", () => {
  it("reports unavailable when navigator.gpu is missing", async () => {
    vi.stubGlobal("navigator", {});
    await expect(detectWebGPU()).resolves.toEqual({ available: false });
  });

  it("reports unavailable when no adapter is returned", async () => {
    vi.stubGlobal("navigator", { gpu: { requestAdapter: async () => null } });
    await expect(detectWebGPU()).resolves.toEqual({ available: false });
  });

  it("reports the adapter label when available", async () => {
    vi.stubGlobal("navigator", {
      gpu: {
        requestAdapter: async () => ({
          info: { vendor: "test", architecture: "software" },
        }),
      },
    });
    await expect(detectWebGPU()).resolves.toEqual({
      available: true,
      adapter: "test / software",
    });
  });

  it("reports unavailable when requestAdapter throws", async () => {
    vi.stubGlobal("navigator", {
      gpu: {
        requestAdapter: async () => {
          throw new Error("no adapter");
        },
      },
    });
    await expect(detectWebGPU()).resolves.toEqual({ available: false });
  });
});
