import type {
  ImportItem,
  CollectionRow,
  HealthResponse,
  ImportResponse,
  SearchResponse,
  StatsResponse,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const body = (await response.json().catch(() => null)) as
    | { error?: string }
    | T
    | null;
  if (!response.ok) {
    const message =
      body && typeof body === "object" && "error" in body && body.error
        ? body.error
        : `HTTP ${response.status}`;
    throw new Error(message);
  }
  return body as T;
}

export const api = {
  health(): Promise<HealthResponse> {
    return request<HealthResponse>("/api/health");
  },

  collections(): Promise<{ collections: CollectionRow[] }> {
    return request<{ collections: CollectionRow[] }>("/api/collections");
  },

  stats(collection: string): Promise<StatsResponse> {
    return request<StatsResponse>(`/api/stats?collection=${encodeURIComponent(collection)}`);
  },

  importItems(
    collection: string,
    items: ImportItem[],
    refresh = true,
  ): Promise<ImportResponse> {
    return request<ImportResponse>("/api/import", {
      method: "POST",
      body: JSON.stringify({ collection, items, refresh }),
    });
  },

  search(
    collection: string,
    question: string,
    embedding: number[],
    limit: number,
  ): Promise<SearchResponse> {
    return request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify({ collection, question, embedding, limit }),
    });
  },

  refresh(collection: string): Promise<{ collection: string; refreshed: boolean; durationMs: number }> {
    return request("/api/refresh", {
      method: "POST",
      body: JSON.stringify({ collection }),
    });
  },

  reset(collection: string): Promise<{ collection: string; reset: boolean; count: number }> {
    return request("/api/reset", {
      method: "POST",
      body: JSON.stringify({ collection }),
    });
  },
};
