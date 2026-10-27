export function recallAtK(retrieved: string[], relevant: string[], k: number): number {
  const relevantSet = new Set(relevant);
  if (relevantSet.size === 0) return 0;
  const top = retrieved.slice(0, Math.max(0, k));
  let hits = 0;
  for (const id of new Set(top)) {
    if (relevantSet.has(id)) hits += 1;
  }
  return hits / relevantSet.size;
}

export function ndcgAtK(retrieved: string[], relevant: string[], k: number): number {
  const relevantSet = new Set(relevant);
  if (relevantSet.size === 0) return 0;
  const top = retrieved.slice(0, Math.max(0, k));
  let dcg = 0;
  top.forEach((id, index) => {
    if (relevantSet.has(id)) dcg += 1 / Math.log2(index + 2);
  });
  let idcg = 0;
  for (let index = 0; index < Math.min(relevantSet.size, k); index += 1) {
    idcg += 1 / Math.log2(index + 2);
  }
  return idcg === 0 ? 0 : dcg / idcg;
}

export function average(values: number[]): number {
  if (!values.length) return 0;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

export function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[middle];
  return (sorted[middle - 1] + sorted[middle]) / 2;
}

export interface QueryMetric {
  id: string;
  question: string;
  relevant: string[];
  retrieved: string[];
  recall: number;
  ndcg: number;
}

export function evaluateQueries(
  queries: { id: string; question: string; relevant: string[] }[],
  resultsById: Record<string, string[]>,
  k: number,
): { recall: number; ndcg: number; perQuery: QueryMetric[] } {
  const perQuery = queries.map((query) => {
    const retrieved = resultsById[query.id] ?? [];
    return {
      id: query.id,
      question: query.question,
      relevant: query.relevant,
      retrieved,
      recall: recallAtK(retrieved, query.relevant, k),
      ndcg: ndcgAtK(retrieved, query.relevant, k),
    };
  });
  return {
    recall: average(perQuery.map((query) => query.recall)),
    ndcg: average(perQuery.map((query) => query.ndcg)),
    perQuery,
  };
}
